export const UUID =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function hasRequiredKeys(
    value: Record<string, unknown>,
    required: readonly string[],
): boolean {
    return required.every((key) => key in value)
}

export function uuid(value: unknown): value is string {
    return typeof value === 'string' && UUID.test(value)
}

export function yuanFromCents(cents: number): string {
    return (cents / 100).toLocaleString('zh-CN', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })
}

export function moneyLabel(cents: number): string {
    return `¥ ${yuanFromCents(cents)}`
}

export function dateLabel(value: string): string {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value)
    if (!match) return value
    return `${match[1]}年${Number(match[2])}月${Number(match[3])}日`
}

export function dateShort(value: string): string {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value)
    if (!match) return value
    return `${match[2]}-${match[3]}`
}

export function dateTimeShort(value: string): string {
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return value
    const stamp = date.toLocaleString('sv-SE', { timeZone: 'Asia/Shanghai' })
    return stamp.slice(5, 16)
}

export function bytesLabel(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function centsFromYuan(value: string): number | null {
    const normalized = value.trim()
    if (!/^\d+(\.\d{1,2})?$/.test(normalized)) return null
    const cents = Math.round(Number(normalized) * 100)
    if (!Number.isInteger(cents) || cents < 1) return null
    return cents
}
