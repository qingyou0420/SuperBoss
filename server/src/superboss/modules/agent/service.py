"""霜月 conversations, tool loop, cards, SOUL, and memory."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from superboss.core.actors import Actor, require_owner
from superboss.core.errors import ConflictError, NotFoundError
from superboss.core.llm import LLMClient, LLMResult, LLMUnavailable, OfflineLLM, iter_llm
from superboss.core.security import utcnow
from superboss.modules.agent.cards import commit_card, parse_card_payload
from superboss.modules.agent.models import (
    AgentCard,
    AgentConversation,
    AgentMemory,
    AgentMessage,
    AgentSoulVersion,
    CardStatus,
    MemoryKind,
    MemoryStatus,
    MessageRole,
)
from superboss.modules.agent.schemas import (
    CardPatch,
    CardRead,
    CardRevise,
    ChatMessageCreate,
    ChatTurnRead,
    ConversationRead,
    MemoryPatch,
    MemoryRead,
    MessageRead,
    SoulPreview,
    SoulRead,
    SoulWrite,
)
from superboss.modules.agent.soul import DEFAULT_SOUL, assemble_system_prompt
from superboss.modules.agent.tools import TOOLS, ToolContext, execute_tool
from superboss.modules.audit.service import AuditService
from superboss.modules.files.models import File, FileState
from superboss.modules.files.storage import ObjectStorage
from superboss.modules.knowledge.extract import ExtractError, extract_text

_WINDOW = 16
_MAX_TOOL_ROUNDS = 6
_OFFLINE = "霜月暂时离线，你仍可以直接使用各页面录入。"
_ATTACHMENT_BYTES = 200_000
_ATTACHMENT_CHARS = 1500


def format_attachment_excerpt(filename: str, *, state: FileState, data: bytes | None) -> str:
    if state != FileState.CLEAN:
        return f"[附件 {filename} 尚未通过扫描，暂未抽取文字]"
    if data is None:
        return f"[附件 {filename}]"
    try:
        text = extract_text(filename, data)
    except ExtractError as error:
        return f"[附件 {filename}：{error}]"
    return f"[附件 {filename}]\n{text[:_ATTACHMENT_CHARS]}"


class AgentService:
    def __init__(
        self,
        session: AsyncSession,
        actor: Actor,
        llm: LLMClient | None = None,
        storage: ObjectStorage | None = None,
        audit: AuditService | None = None,
        enqueue_extract: Callable[[UUID], Awaitable[None] | None] | None = None,
    ) -> None:
        require_owner(actor)
        self.session = session
        self.actor = actor
        self.llm = llm or OfflineLLM()
        self.storage = storage
        self.audit = audit
        self.enqueue_extract = enqueue_extract or (lambda _conversation_id: None)

    async def _conversation(self, conversation_id: UUID) -> AgentConversation:
        conversation = await self.session.get(AgentConversation, conversation_id)
        if conversation is None or conversation.owner_id != self.actor.subject_id:
            raise NotFoundError("CONVERSATION_NOT_FOUND", "Conversation not found")
        return conversation

    async def list_conversations(self, query: str | None = None) -> list[ConversationRead]:
        statement = (
            select(AgentConversation)
            .where(
                AgentConversation.owner_id == self.actor.subject_id,
                AgentConversation.archived_at.is_(None),
            )
            .order_by(AgentConversation.last_message_at.desc())
        )
        needle = (query or "").strip()[:80]
        if needle:
            pattern = f"%{needle}%"
            statement = statement.where(
                or_(
                    AgentConversation.title.ilike(pattern),
                    AgentConversation.summary.ilike(pattern),
                )
            )
        rows = (await self.session.scalars(statement)).all()
        return [ConversationRead.model_validate(item) for item in rows]

    async def create_conversation(self, title: str | None = None) -> ConversationRead:
        conversation = AgentConversation(
            owner_id=self.actor.subject_id,
            title=(title or "新对话")[:80],
        )
        self.session.add(conversation)
        await self.session.flush()
        return ConversationRead.model_validate(conversation)

    async def archive_conversation(self, conversation_id: UUID) -> None:
        conversation = await self._conversation(conversation_id)
        conversation.archived_at = utcnow()

    async def list_messages(self, conversation_id: UUID) -> list[MessageRead]:
        await self._conversation(conversation_id)
        rows = (
            await self.session.scalars(
                select(AgentMessage)
                .where(
                    AgentMessage.conversation_id == conversation_id,
                    AgentMessage.role.in_((MessageRole.USER, MessageRole.ASSISTANT)),
                )
                .order_by(AgentMessage.created_at)
            )
        ).all()
        return [MessageRead.model_validate(item) for item in rows]

    async def list_cards(self, conversation_id: UUID) -> list[CardRead]:
        await self._conversation(conversation_id)
        rows = (
            await self.session.scalars(
                select(AgentCard)
                .where(AgentCard.conversation_id == conversation_id)
                .order_by(AgentCard.id)
            )
        ).all()
        return [CardRead.model_validate(item) for item in rows]

    async def _begin_turn(
        self, conversation_id: UUID, command: ChatMessageCreate
    ) -> tuple[AgentConversation, str]:
        conversation = await self._conversation(conversation_id)
        content = command.content
        if command.file_id is not None:
            excerpt = await self._attachment_excerpt(command.file_id)
            content = f"{content}\n\n{excerpt}".strip() if content else excerpt
        self.session.add(
            AgentMessage(
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content=content,
            )
        )
        if conversation.title in {"", "新对话"}:
            conversation.title = content[:40] or "新对话"
        conversation.last_message_at = utcnow()
        await self.session.flush()
        return conversation, content

    async def _attachment_excerpt(self, file_id: UUID) -> str:
        file = await self.session.get(File, file_id)
        if file is None:
            raise NotFoundError("FILE_NOT_FOUND", "File not found")
        data: bytes | None = None
        if file.state == FileState.CLEAN and self.storage is not None:
            chunks: list[bytes] = []
            total = 0
            async for chunk in self.storage.stream(file.object_key):
                chunks.append(chunk)
                total += len(chunk)
                if total >= _ATTACHMENT_BYTES:
                    break
            data = b"".join(chunks)
        return format_attachment_excerpt(file.filename, state=file.state, data=data)

    async def _finish_turn(
        self, conversation: AgentConversation, assistant: AgentMessage
    ) -> ChatTurnRead:
        try:
            self.enqueue_extract(conversation.id)
        except Exception:  # noqa: BLE001,S110 -- extract is best-effort
            pass
        cards = [
            CardRead.model_validate(item)
            for item in (
                await self.session.scalars(
                    select(AgentCard).where(AgentCard.message_id == assistant.id)
                )
            ).all()
        ]
        return ChatTurnRead(
            conversation_id=conversation.id,
            message=MessageRead.model_validate(assistant),
            cards=cards,
            offline=False,
        )

    async def chat(self, conversation_id: UUID, command: ChatMessageCreate) -> ChatTurnRead:
        conversation, content = await self._begin_turn(conversation_id, command)
        if not self.llm.available:
            assistant = await self._offline_reply(conversation)
            return ChatTurnRead(
                conversation_id=conversation.id,
                message=MessageRead.model_validate(assistant),
                cards=[],
                offline=True,
            )
        assistant = await self._run_turn(conversation, content)
        return await self._finish_turn(conversation, assistant)

    async def chat_stream(
        self, conversation_id: UUID, command: ChatMessageCreate
    ) -> AsyncIterator[tuple[str, object]]:
        conversation, content = await self._begin_turn(conversation_id, command)
        if not self.llm.available:
            assistant = await self._offline_reply(conversation)
            yield ("offline", _OFFLINE)
            yield (
                "done",
                ChatTurnRead(
                    conversation_id=conversation.id,
                    message=MessageRead.model_validate(assistant),
                    cards=[],
                    offline=True,
                ),
            )
            return
        queue: asyncio.Queue[tuple[str, object] | None] = asyncio.Queue()

        async def on_token(piece: str) -> None:
            await queue.put(("token", piece))

        async def worker() -> None:
            try:
                assistant = await self._run_turn(conversation, content, on_token=on_token)
                turn = await self._finish_turn(conversation, assistant)
                await queue.put(("done", turn))
            except Exception as error:  # noqa: BLE001 -- surface to the SSE consumer
                await queue.put(("error", error))
            finally:
                await queue.put(None)

        task = asyncio.create_task(worker())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                kind, payload = item
                if kind == "error":
                    assert isinstance(payload, BaseException)
                    raise payload
                yield item
        finally:
            await task

    async def _offline_reply(self, conversation: AgentConversation) -> AgentMessage:
        assistant = AgentMessage(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content=_OFFLINE,
        )
        self.session.add(assistant)
        conversation.last_message_at = utcnow()
        await self.session.flush()
        return assistant

    async def _run_turn(
        self,
        conversation: AgentConversation,
        user_text: str,
        on_token: Callable[[str], Awaitable[None]] | None = None,
    ) -> AgentMessage:
        memories = await self.recall(user_text)
        soul = await self.active_soul_text()
        prompt = assemble_system_prompt(
            soul,
            "\n".join(f"- {item['content']}" for item in memories),
            conversation.summary,
        )
        history = await self._window(conversation.id)
        llm_messages: list[dict[str, Any]] = [{"role": "system", "content": prompt}, *history]
        context = ToolContext(
            session=self.session,
            actor=self.actor,
            storage=self.storage,
            conversation_id=conversation.id,
            recall=self.recall,
        )
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        last_content = ""
        try:
            for _round in range(_MAX_TOOL_ROUNDS):
                result = LLMResult(content="")
                async for chunk in iter_llm(self.llm, llm_messages, TOOLS):
                    if chunk.content:
                        result.content += chunk.content
                        if on_token is not None:
                            await on_token(chunk.content)
                    if chunk.done:
                        result.tool_calls = chunk.tool_calls or []
                        usage["prompt_tokens"] += chunk.prompt_tokens
                        usage["completion_tokens"] += chunk.completion_tokens
                last_content = result.content
                if not result.tool_calls:
                    break
                assistant_tool = {
                    "role": "assistant",
                    "content": result.content or None,
                    "tool_calls": [
                        {
                            "id": item.id or str(uuid4()),
                            "type": "function",
                            "function": {"name": item.name, "arguments": item.arguments},
                        }
                        for item in result.tool_calls
                    ],
                }
                llm_messages.append(assistant_tool)
                self.session.add(
                    AgentMessage(
                        conversation_id=conversation.id,
                        role=MessageRole.ASSISTANT,
                        content=result.content,
                        tool_calls={"tool_calls": assistant_tool["tool_calls"]},
                    )
                )
                for call in result.tool_calls:
                    output = await execute_tool(context, call.name, call.arguments)
                    llm_messages.append(
                        {"role": "tool", "tool_call_id": call.id, "content": output}
                    )
                    self.session.add(
                        AgentMessage(
                            conversation_id=conversation.id,
                            role=MessageRole.TOOL,
                            content=output,
                        )
                    )
            else:
                last_content = last_content or "工具调用次数已达上限，请拆成更小的请求。"
        except LLMUnavailable:
            last_content = _OFFLINE
        assistant = AgentMessage(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content=last_content or "请确认下面的卡片，或告诉我要怎么改。",
            card_ids=[item.id for item in context.pending_cards],
            token_usage=usage,
        )
        self.session.add(assistant)
        await self.session.flush()
        for card in context.pending_cards:
            card.message_id = assistant.id
        conversation.last_message_at = utcnow()
        await self.session.flush()
        return assistant

    async def _window(self, conversation_id: UUID) -> list[dict[str, Any]]:
        rows = list(
            (
                await self.session.scalars(
                    select(AgentMessage)
                    .where(AgentMessage.conversation_id == conversation_id)
                    .order_by(AgentMessage.created_at.desc())
                    .limit(_WINDOW)
                )
            ).all()
        )
        rows.reverse()
        messages: list[dict[str, Any]] = []
        for item in rows:
            if item.role is MessageRole.TOOL:
                messages.append({"role": "tool", "content": item.content})
            else:
                messages.append({"role": item.role.value, "content": item.content})
        return messages

    async def confirm_card(self, card_id: UUID, request_id: UUID) -> CardRead:
        card = await self._card(card_id)
        if card.status is not CardStatus.PROPOSED:
            raise ConflictError("CARD_NOT_OPEN", "Card is not waiting for confirmation")
        parse_card_payload(card.kind, card.payload)
        await commit_card(
            self.session,
            self.actor,
            card,
            request_id=request_id,
            storage=self.storage,
            audit=self.audit,
        )
        self.session.add(
            AgentMessage(
                conversation_id=card.conversation_id,
                role=MessageRole.SYSTEM,
                content=f"已入库：{card.kind.value}",
            )
        )
        return CardRead.model_validate(card)

    async def reject_card(self, card_id: UUID) -> CardRead:
        card = await self._card(card_id)
        if card.status is not CardStatus.PROPOSED:
            raise ConflictError("CARD_NOT_OPEN", "Card is not waiting for confirmation")
        card.status = CardStatus.REJECTED
        card.decided_at = utcnow()
        return CardRead.model_validate(card)

    async def patch_card(self, card_id: UUID, command: CardPatch) -> CardRead:
        card = await self._card(card_id)
        if card.status is not CardStatus.PROPOSED:
            raise ConflictError("CARD_NOT_OPEN", "Card is not waiting for confirmation")
        merged = {**card.payload, **command.payload}
        parsed = parse_card_payload(card.kind, merged)
        card.payload = parsed.model_dump(mode="json")
        if command.note:
            self.session.add(
                AgentMessage(
                    conversation_id=card.conversation_id,
                    role=MessageRole.SYSTEM,
                    content=f"老板修改了卡片：{command.note}",
                )
            )
        return CardRead.model_validate(card)

    async def revise_card(self, card_id: UUID, command: CardRevise) -> ChatTurnRead:
        card = await self._card(card_id)
        if card.status is not CardStatus.PROPOSED:
            raise ConflictError("CARD_NOT_OPEN", "Card is not waiting for confirmation")
        card.status = CardStatus.REVISED
        card.decided_at = utcnow()
        instruction = (
            f"请按以下意见修改这张{card.kind.value}卡片，生成新卡片替换它。"
            f"原内容：{card.payload}。意见：{command.instruction}"
        )
        return await self.chat(card.conversation_id, ChatMessageCreate(content=instruction))

    async def _card(self, card_id: UUID) -> AgentCard:
        card = await self.session.get(AgentCard, card_id)
        if card is None:
            raise NotFoundError("CARD_NOT_FOUND", "Card not found")
        conversation = await self.session.get(AgentConversation, card.conversation_id)
        if conversation is None or conversation.owner_id != self.actor.subject_id:
            raise NotFoundError("CARD_NOT_FOUND", "Card not found")
        return card

    async def active_soul_text(self) -> str:
        soul = await self.session.scalar(
            select(AgentSoulVersion).where(AgentSoulVersion.is_active.is_(True))
        )
        if soul is None:
            soul = AgentSoulVersion(content=DEFAULT_SOUL, note="默认", is_active=True)
            self.session.add(soul)
            await self.session.flush()
        return soul.content

    async def list_soul(self) -> list[SoulRead]:
        await self.active_soul_text()
        rows = (
            await self.session.scalars(
                select(AgentSoulVersion).order_by(AgentSoulVersion.created_at.desc())
            )
        ).all()
        return [SoulRead.model_validate(item) for item in rows]

    async def write_soul(self, command: SoulWrite, request_id: UUID | None = None) -> SoulRead:
        current = (
            await self.session.scalars(
                select(AgentSoulVersion).where(AgentSoulVersion.is_active.is_(True))
            )
        ).all()
        for item in current:
            item.is_active = False
        soul = AgentSoulVersion(content=command.content, note=command.note, is_active=True)
        self.session.add(soul)
        await self.session.flush()
        await self._audit_soul("agent.soul.write", soul.id, request_id)
        return SoulRead.model_validate(soul)

    async def activate_soul(self, soul_id: UUID, request_id: UUID | None = None) -> SoulRead:
        target = await self.session.get(AgentSoulVersion, soul_id)
        if target is None:
            raise NotFoundError("SOUL_NOT_FOUND", "SOUL version not found")
        current = (
            await self.session.scalars(
                select(AgentSoulVersion).where(AgentSoulVersion.is_active.is_(True))
            )
        ).all()
        for item in current:
            item.is_active = False
        target.is_active = True
        await self.session.flush()
        await self._audit_soul("agent.soul.activate", target.id, request_id)
        return SoulRead.model_validate(target)

    async def _audit_soul(self, action: str, soul_id: UUID, request_id: UUID | None) -> None:
        if self.audit is None or request_id is None:
            return
        from superboss.modules.audit.schemas import AuditEventInput

        await self.audit.record(
            AuditEventInput(
                actor=self.actor,
                action=action,
                object_type="agent_soul",
                object_id=soul_id,
                outcome="SUCCESS",
                request_id=request_id,
            )
        )

    async def preview_soul(self) -> SoulPreview:
        return SoulPreview(prompt=assemble_system_prompt(await self.active_soul_text(), "", ""))

    async def list_memories(self) -> list[MemoryRead]:
        rows = (
            await self.session.scalars(
                select(AgentMemory)
                .where(AgentMemory.status == MemoryStatus.ACTIVE)
                .order_by(AgentMemory.pinned.desc(), AgentMemory.created_at.desc())
            )
        ).all()
        return [MemoryRead.model_validate(item) for item in rows]

    async def patch_memory(self, memory_id: UUID, command: MemoryPatch) -> MemoryRead:
        memory = await self.session.get(AgentMemory, memory_id)
        if memory is None:
            raise NotFoundError("MEMORY_NOT_FOUND", "Memory not found")
        if command.content is not None:
            memory.content = command.content
        if command.pinned is not None:
            memory.pinned = command.pinned
        if command.status is not None:
            memory.status = command.status
        if command.importance is not None:
            memory.importance = command.importance
        await self.session.flush()
        return MemoryRead.model_validate(memory)

    async def recall(self, query: str) -> list[dict[str, str]]:
        pinned = list(
            (
                await self.session.scalars(
                    select(AgentMemory).where(
                        AgentMemory.status == MemoryStatus.ACTIVE,
                        AgentMemory.pinned.is_(True),
                    )
                )
            ).all()
        )
        digest = list(
            (
                await self.session.scalars(
                    select(AgentMemory)
                    .where(
                        AgentMemory.status == MemoryStatus.ACTIVE,
                        AgentMemory.kind == MemoryKind.DAILY_DIGEST,
                        AgentMemory.created_at >= utcnow() - timedelta(days=7),
                    )
                    .order_by(AgentMemory.created_at.desc())
                    .limit(7)
                )
            ).all()
        )
        searched: list[AgentMemory] = []
        needle = query.strip()[:80]
        if needle:
            searched = list(
                (
                    await self.session.scalars(
                        select(AgentMemory)
                        .where(
                            AgentMemory.status == MemoryStatus.ACTIVE,
                            AgentMemory.content.ilike(f"%{needle}%"),
                        )
                        .order_by(AgentMemory.importance.desc())
                        .limit(8)
                    )
                ).all()
            )
        seen: set[UUID] = set()
        items: list[dict[str, str]] = []
        for memory in [*pinned, *digest, *searched]:
            if memory.id in seen:
                continue
            seen.add(memory.id)
            memory.recall_count += 1
            memory.last_recalled_at = utcnow()
            items.append(
                {"id": str(memory.id), "kind": memory.kind.value, "content": memory.content}
            )
        return items[:12]

    async def extract_memories(self, conversation_id: UUID) -> None:
        if not self.llm.available:
            return
        rows = list(
            (
                await self.session.scalars(
                    select(AgentMessage)
                    .where(
                        AgentMessage.conversation_id == conversation_id,
                        AgentMessage.role.in_((MessageRole.USER, MessageRole.ASSISTANT)),
                    )
                    .order_by(AgentMessage.created_at.desc())
                    .limit(8)
                )
            ).all()
        )
        if not rows:
            return
        transcript = "\n".join(f"{item.role.value}: {item.content}" for item in reversed(rows))
        prompt = (
            "从对话中提取稳定偏好或事实。只返回 JSON 数组，"
            '每项 {"kind":"PREFERENCE|FACT|DECISION|PROJECT_NOTE","content":"...","importance":1-5}。'
            "没有则返回 []。不要提取一次性金额。"
        )
        try:
            result = await self.llm.complete(
                [{"role": "system", "content": prompt}, {"role": "user", "content": transcript}],
                [],
            )
        except LLMUnavailable:
            return
        try:
            payload = json.loads(result.content)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, list):
            return
        existing = list(
            (
                await self.session.scalars(
                    select(AgentMemory).where(AgentMemory.status == MemoryStatus.ACTIVE)
                )
            ).all()
        )
        for item in payload[:8]:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if len(content) < 4:
                continue
            if any(memory.content == content for memory in existing):
                continue
            kind_raw = str(item.get("kind") or "FACT")
            try:
                kind = MemoryKind(kind_raw)
            except ValueError:
                kind = MemoryKind.FACT
            importance = item.get("importance")
            memory = AgentMemory(
                kind=kind,
                content=content[:2000],
                importance=int(importance)
                if isinstance(importance, int) and 1 <= importance <= 5
                else 3,
                status=MemoryStatus.ACTIVE,
            )
            self.session.add(memory)
            existing.append(memory)
        count = await self.session.scalar(
            select(func.count())
            .select_from(AgentMemory)
            .where(AgentMemory.status == MemoryStatus.ACTIVE)
        )
        if count and count > 2000:
            extras = list(
                (
                    await self.session.scalars(
                        select(AgentMemory)
                        .where(
                            AgentMemory.status == MemoryStatus.ACTIVE, AgentMemory.pinned.is_(False)
                        )
                        .order_by(AgentMemory.recall_count.asc(), AgentMemory.created_at.asc())
                        .limit(int(count) - 2000)
                    )
                ).all()
            )
            for memory in extras:
                memory.status = MemoryStatus.ARCHIVED
        await self.session.flush()
