import { dateLabel, moneyLabel, yuanFromCents } from '../../api/parse'
import type { AgentCard, CardKind } from '../../api/agent'
import {
    CARD_KIND_LABEL,
    FIELD_LABEL,
    FINANCE_KIND_LABEL,
    FINANCE_SCOPE_LABEL,
    FOLDER_NAME,
    STAGE_LABEL,
    VISIBILITY_LABEL,
} from '../../copy/glossary'
import { chatCopy } from '../../copy/pages/chat'

export interface CardRow {
    label: string
    value: string
    warn?: boolean
}

export interface CardField {
    key: string
    label: string
    type: 'text' | 'money' | 'date' | 'select' | 'project'
    options?: { value: string; label: string }[]
}

export function kindLabel(kind: CardKind | string): string {
    return CARD_KIND_LABEL[kind as CardKind] || kind
}

export function headline(
    card: AgentCard,
    projectNames: Record<string, string>,
): string {
    const payload = card.payload
    if (typeof payload.category === 'string') return payload.category
    if (typeof payload.name === 'string') return payload.name
    if (typeof payload.title === 'string') return payload.title
    if (typeof payload.content === 'string') return payload.content.slice(0, 24)
    if (typeof payload.filename === 'string') return payload.filename
    if (typeof payload.project_id === 'string') {
        return projectNames[payload.project_id] || FIELD_LABEL.project
    }
    return kindLabel(card.kind)
}

export function displayRows(
    card: AgentCard,
    projectNames: Record<string, string>,
    folderNames: Record<string, string> = {},
): CardRow[] {
    const payload = card.payload
    const rows: CardRow[] = []
    const push = (label: string, value: string, warn = false) => {
        if (value) rows.push({ label, value, warn })
    }

    if (typeof payload.category === 'string')
        push(FIELD_LABEL.category, payload.category)
    if (typeof payload.amount_cents === 'number')
        push(FIELD_LABEL.amount, moneyLabel(payload.amount_cents))
    if (typeof payload.kind === 'string' && card.kind !== 'finance_entry') {
        push(
            FIELD_LABEL.kind,
            FINANCE_KIND_LABEL[
                payload.kind as keyof typeof FINANCE_KIND_LABEL
            ] || payload.kind,
        )
    }
    if (typeof payload.scope === 'string') {
        push(
            FIELD_LABEL.scope,
            FINANCE_SCOPE_LABEL[
                payload.scope as keyof typeof FINANCE_SCOPE_LABEL
            ] || payload.scope,
        )
    }
    if (typeof payload.project_id === 'string') {
        push(
            FIELD_LABEL.project,
            projectNames[payload.project_id] || FIELD_LABEL.project,
        )
    }
    if (typeof payload.visibility === 'string') {
        push(
            FIELD_LABEL.visibility,
            VISIBILITY_LABEL[
                payload.visibility as keyof typeof VISIBILITY_LABEL
            ] || payload.visibility,
        )
    }
    if (typeof payload.name === 'string' && card.kind !== 'finance_entry') {
        push(FIELD_LABEL.name, payload.name)
    }
    if (typeof payload.stage === 'string') {
        push(
            FIELD_LABEL.stage,
            STAGE_LABEL[payload.stage as keyof typeof STAGE_LABEL] ||
                payload.stage,
        )
    }
    if (typeof payload.title === 'string')
        push(FIELD_LABEL.title, payload.title)
    if (typeof payload.due_on === 'string')
        push(FIELD_LABEL.due, dateLabel(payload.due_on))
    if (typeof payload.occurred_on === 'string')
        push(FIELD_LABEL.date, dateLabel(payload.occurred_on))
    if (typeof payload.memo === 'string') push(FIELD_LABEL.memo, payload.memo)
    if (typeof payload.content === 'string' && card.kind === 'memory') {
        push(FIELD_LABEL.content, payload.content)
    }
    if (typeof payload.filename === 'string')
        push(FIELD_LABEL.file, payload.filename)
    if (typeof payload.target_folder_id === 'string') {
        const folder =
            folderNames[payload.target_folder_id] || FIELD_LABEL.folder
        push(FIELD_LABEL.targetFolder, folder)
        if (folder === FOLDER_NAME.PROJECTS)
            push(FIELD_LABEL.visibilityWillBecome, VISIBILITY_LABEL.ALL, true)
    }
    if (typeof payload.new_doc_title === 'string')
        push(FIELD_LABEL.document, payload.new_doc_title)
    if (rows.length === 0) {
        for (const [key, value] of Object.entries(payload)) {
            if (key.endsWith('_id') || value === null || value === undefined)
                continue
            push(key, String(value))
        }
    }
    return rows
}

export function editFields(kind: CardKind): CardField[] {
    if (kind === 'finance_entry' || kind === 'finance_adjust') {
        return [
            { key: 'category', label: FIELD_LABEL.category, type: 'text' },
            { key: 'amount_cents', label: FIELD_LABEL.amount, type: 'money' },
            {
                key: 'scope',
                label: FIELD_LABEL.scope,
                type: 'select',
                options: [
                    {
                        value: 'COMPANY',
                        label: FINANCE_SCOPE_LABEL.COMPANY,
                    },
                    {
                        value: 'PROJECT',
                        label: FINANCE_SCOPE_LABEL.PROJECT,
                    },
                ],
            },
            { key: 'project_id', label: FIELD_LABEL.project, type: 'project' },
            {
                key: 'visibility',
                label: FIELD_LABEL.visibility,
                type: 'select',
                options: [
                    { value: 'ALL', label: VISIBILITY_LABEL.ALL },
                    {
                        value: 'MANAGEMENT',
                        label: VISIBILITY_LABEL.MANAGEMENT,
                    },
                    {
                        value: 'OWNER_ONLY',
                        label: VISIBILITY_LABEL.OWNER_ONLY,
                    },
                ],
            },
            { key: 'occurred_on', label: FIELD_LABEL.date, type: 'date' },
            { key: 'memo', label: FIELD_LABEL.memo, type: 'text' },
        ]
    }
    if (kind === 'project_create' || kind === 'project_update') {
        return [
            { key: 'name', label: FIELD_LABEL.name, type: 'text' },
            {
                key: 'stage',
                label: FIELD_LABEL.stage,
                type: 'select',
                options: [
                    { value: 'PLANNING', label: STAGE_LABEL.PLANNING },
                    { value: 'ACTIVE', label: STAGE_LABEL.ACTIVE },
                    { value: 'DELIVERING', label: STAGE_LABEL.DELIVERING },
                    { value: 'REVIEW', label: STAGE_LABEL.REVIEW },
                    { value: 'ARCHIVED', label: STAGE_LABEL.ARCHIVED },
                ],
            },
            { key: 'starts_on', label: FIELD_LABEL.starts, type: 'date' },
            { key: 'due_on', label: FIELD_LABEL.due, type: 'date' },
            {
                key: 'description',
                label: FIELD_LABEL.description,
                type: 'text',
            },
        ]
    }
    if (kind === 'milestone_change') {
        return [
            { key: 'title', label: FIELD_LABEL.title, type: 'text' },
            { key: 'due_on', label: FIELD_LABEL.due, type: 'date' },
        ]
    }
    if (kind === 'memory') {
        return [{ key: 'content', label: FIELD_LABEL.content, type: 'text' }]
    }
    if (kind === 'knowledge_ingest') {
        return [
            { key: 'new_doc_title', label: FIELD_LABEL.title, type: 'text' },
        ]
    }
    if (kind === 'file_move') {
        return [{ key: 'new_name', label: FIELD_LABEL.name, type: 'text' }]
    }
    return []
}

export function committedLabel(kind: string): string {
    if (kind.startsWith('finance')) return chatCopy.finance
    if (kind === 'file_move') return chatCopy.drive
    if (kind === 'knowledge_ingest') return chatCopy.knowledge
    if (kind === 'memory') return chatCopy.memory
    return chatCopy.projects
}

export function draftsFromPayload(
    payload: Record<string, unknown>,
): Record<string, string> {
    const drafts: Record<string, string> = {}
    for (const [key, value] of Object.entries(payload)) {
        if (key === 'amount_cents' && typeof value === 'number') {
            drafts[key] = yuanFromCents(value)
            continue
        }
        if (value === null || value === undefined) continue
        drafts[key] = String(value)
    }
    return drafts
}

export function payloadFromDrafts(
    drafts: Record<string, string>,
    original: Record<string, unknown>,
): Record<string, unknown> {
    const payload: Record<string, unknown> = { ...original }
    for (const [key, raw] of Object.entries(drafts)) {
        if (key === 'amount_cents') {
            const cents = Math.round(Number(raw) * 100)
            payload[key] = Number.isFinite(cents) ? cents : original[key]
            continue
        }
        payload[key] = raw
    }
    return payload
}

export function committedHref(card: AgentCard): string {
    if (card.kind.startsWith('finance')) return '/finance'
    if (card.kind === 'file_move') return '/drive'
    if (card.kind === 'knowledge_ingest') return '/knowledge'
    if (card.kind === 'memory') return '/memory'
    if (card.committed_object_id && card.kind.startsWith('project')) {
        return `/projects/${card.committed_object_id}`
    }
    return '/projects'
}
