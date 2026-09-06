import { moneyLabel, yuanFromCents } from '../../api/parse'
import type { AgentCard, CardKind } from '../../api/agent'
import {
    CARD_KIND_LABEL,
    FINANCE_KIND_LABEL,
    FINANCE_SCOPE_LABEL,
    STAGE_LABEL,
    VISIBILITY_LABEL,
} from '../../copy/glossary'

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
        return projectNames[payload.project_id] || '项目'
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

    if (typeof payload.category === 'string') push('类别', payload.category)
    if (typeof payload.amount_cents === 'number')
        push('金额', moneyLabel(payload.amount_cents))
    if (typeof payload.kind === 'string' && card.kind !== 'finance_entry') {
        push(
            '类型',
            FINANCE_KIND_LABEL[
                payload.kind as keyof typeof FINANCE_KIND_LABEL
            ] || payload.kind,
        )
    }
    if (typeof payload.scope === 'string') {
        push(
            '范围',
            FINANCE_SCOPE_LABEL[
                payload.scope as keyof typeof FINANCE_SCOPE_LABEL
            ] || payload.scope,
        )
    }
    if (typeof payload.project_id === 'string') {
        push('项目', projectNames[payload.project_id] || '项目')
    }
    if (typeof payload.visibility === 'string') {
        push(
            '可见范围',
            VISIBILITY_LABEL[
                payload.visibility as keyof typeof VISIBILITY_LABEL
            ] || payload.visibility,
        )
    }
    if (typeof payload.name === 'string' && card.kind !== 'finance_entry') {
        push('名称', payload.name)
    }
    if (typeof payload.stage === 'string') {
        push(
            '阶段',
            STAGE_LABEL[payload.stage as keyof typeof STAGE_LABEL] ||
                payload.stage,
        )
    }
    if (typeof payload.title === 'string') push('标题', payload.title)
    if (typeof payload.due_on === 'string') push('到期', payload.due_on)
    if (typeof payload.occurred_on === 'string')
        push('日期', payload.occurred_on)
    if (typeof payload.memo === 'string') push('备注', payload.memo)
    if (typeof payload.content === 'string' && card.kind === 'memory') {
        push('内容', payload.content)
    }
    if (typeof payload.filename === 'string') push('文件', payload.filename)
    if (typeof payload.target_folder_id === 'string') {
        const folder = folderNames[payload.target_folder_id] || '目录'
        push('目标目录', folder)
        if (folder === '项目') push('可见范围将变为', '全员', true)
    }
    if (typeof payload.new_doc_title === 'string')
        push('文档', payload.new_doc_title)
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
            { key: 'category', label: '类别', type: 'text' },
            { key: 'amount_cents', label: '金额', type: 'money' },
            {
                key: 'scope',
                label: '范围',
                type: 'select',
                options: [
                    { value: 'COMPANY', label: '公司运营' },
                    { value: 'PROJECT', label: '项目' },
                ],
            },
            { key: 'project_id', label: '项目', type: 'project' },
            {
                key: 'visibility',
                label: '可见范围',
                type: 'select',
                options: [
                    { value: 'ALL', label: '全员' },
                    { value: 'MANAGEMENT', label: '管理层' },
                    { value: 'OWNER_ONLY', label: '仅自己' },
                ],
            },
            { key: 'occurred_on', label: '日期', type: 'date' },
            { key: 'memo', label: '备注', type: 'text' },
        ]
    }
    if (kind === 'project_create' || kind === 'project_update') {
        return [
            { key: 'name', label: '名称', type: 'text' },
            {
                key: 'stage',
                label: '阶段',
                type: 'select',
                options: [
                    { value: 'PLANNING', label: '筹备' },
                    { value: 'ACTIVE', label: '进行' },
                    { value: 'DELIVERING', label: '交付' },
                    { value: 'REVIEW', label: '复盘' },
                    { value: 'ARCHIVED', label: '归档' },
                ],
            },
            { key: 'starts_on', label: '开始', type: 'date' },
            { key: 'due_on', label: '到期', type: 'date' },
            { key: 'description', label: '说明', type: 'text' },
        ]
    }
    if (kind === 'milestone_change') {
        return [
            { key: 'title', label: '标题', type: 'text' },
            { key: 'due_on', label: '到期', type: 'date' },
        ]
    }
    if (kind === 'memory') {
        return [{ key: 'content', label: '内容', type: 'text' }]
    }
    if (kind === 'knowledge_ingest') {
        return [{ key: 'new_doc_title', label: '标题', type: 'text' }]
    }
    return []
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
