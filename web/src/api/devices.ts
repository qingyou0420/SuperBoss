import { apiClient, type BrowserHttpClient } from './http'

const UUID =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const MAX_DEVICES = 1000
const MAX_PROJECTS = 1000

export interface PairingCode {
    readonly raw_code: string
    readonly expires_at: string
}

export interface DeviceProject {
    readonly id: string
    readonly name: string
}

export interface OwnerDevice {
    readonly id: string
    readonly name: string
    readonly paired_at: string
    readonly last_used_at: string | null
    readonly revoked_at: string | null
    readonly status: 'ACTIVE' | 'REVOKED'
    readonly projects: readonly DeviceProject[]
}

export class DeviceContractError extends Error {
    constructor() {
        super('Invalid device data')
        this.name = 'DeviceContractError'
    }
}

function isRecord(value: unknown): value is Record<string, unknown> {
    if (typeof value !== 'object' || value === null || Array.isArray(value))
        return false
    const prototype = Object.getPrototypeOf(value)
    return prototype === Object.prototype || prototype === null
}

function exactKeys(
    value: Record<string, unknown>,
    expected: string[],
): boolean {
    const actual = Object.keys(value).sort()
    return (
        actual.length === expected.length &&
        actual.every((key, index) => key === expected[index])
    )
}

function uuid(value: unknown): value is string {
    return typeof value === 'string' && UUID.test(value)
}

function safeText(value: unknown, maximum: number): value is string {
    if (
        typeof value !== 'string' ||
        !value.trim() ||
        [...value].length > maximum
    )
        return false
    for (let index = 0; index < value.length; index += 1) {
        const code = value.charCodeAt(index)
        if (code <= 31 || (code >= 127 && code <= 159)) return false
        if (code >= 0xd800 && code <= 0xdbff) {
            const next = value.charCodeAt(index + 1)
            if (next < 0xdc00 || next > 0xdfff) return false
            index += 1
        } else if (code >= 0xdc00 && code <= 0xdfff) return false
    }
    return true
}

function timestamp(value: unknown): string {
    if (typeof value !== 'string' || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value)) {
        throw new DeviceContractError()
    }
    const milliseconds = Date.parse(value)
    if (!Number.isFinite(milliseconds)) throw new DeviceContractError()
    return new Date(milliseconds).toISOString()
}

function optionalTimestamp(value: unknown): string | null {
    return value === null ? null : timestamp(value)
}

function canonicalProjects(value: unknown): DeviceProject[] {
    if (!Array.isArray(value) || value.length > MAX_PROJECTS)
        throw new DeviceContractError()
    const ids = new Set<string>()
    return value.map((project): DeviceProject => {
        if (
            !isRecord(project) ||
            !exactKeys(project, ['id', 'name']) ||
            !uuid(project.id) ||
            !safeText(project.name, 255) ||
            ids.has(project.id)
        ) {
            throw new DeviceContractError()
        }
        ids.add(project.id)
        return { id: project.id, name: project.name }
    })
}

function parsePairingCode(value: unknown): PairingCode {
    if (
        !isRecord(value) ||
        !exactKeys(value, ['expires_at', 'raw_code']) ||
        !safeText(value.raw_code, 255)
    ) {
        throw new DeviceContractError()
    }
    return { expires_at: timestamp(value.expires_at), raw_code: value.raw_code }
}

function parseDevice(value: unknown): OwnerDevice {
    if (
        !isRecord(value) ||
        !exactKeys(value, [
            'id',
            'last_used_at',
            'name',
            'paired_at',
            'projects',
            'revoked_at',
            'status',
        ]) ||
        !uuid(value.id) ||
        !safeText(value.name, 255) ||
        (value.status !== 'ACTIVE' && value.status !== 'REVOKED')
    ) {
        throw new DeviceContractError()
    }
    const pairedAt = timestamp(value.paired_at)
    const lastUsedAt = optionalTimestamp(value.last_used_at)
    const revokedAt = optionalTimestamp(value.revoked_at)
    if (
        (value.status === 'ACTIVE' && revokedAt !== null) ||
        (value.status === 'REVOKED' && revokedAt === null) ||
        (lastUsedAt !== null && lastUsedAt < pairedAt) ||
        (revokedAt !== null && revokedAt < pairedAt)
    ) {
        throw new DeviceContractError()
    }
    return {
        id: value.id,
        last_used_at: lastUsedAt,
        name: value.name,
        paired_at: pairedAt,
        projects: canonicalProjects(value.projects),
        revoked_at: revokedAt,
        status: value.status,
    }
}

function canonicalPairingRequest(value: unknown): { project_ids: string[] } {
    if (!isRecord(value) || !exactKeys(value, ['project_ids']))
        throw new DeviceContractError()
    if (!Array.isArray(value.project_ids) || value.project_ids.length < 1)
        throw new DeviceContractError()
    const ids = new Set<string>()
    for (const projectId of value.project_ids) {
        if (!uuid(projectId) || ids.has(projectId))
            throw new DeviceContractError()
        ids.add(projectId)
    }
    return { project_ids: [...ids] }
}

export function deviceErrorMessage(_error: unknown): string {
    void _error
    return '\u8bbe\u5907\u64cd\u4f5c\u5931\u8d25\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002'
}

export function createDevicesApi(client: BrowserHttpClient) {
    return Object.freeze({
        async createPairingCode(command: {
            readonly project_ids: readonly string[]
        }): Promise<PairingCode> {
            const canonical = canonicalPairingRequest(command)
            const response = await client.post(
                '/owner/devices/pairing-codes',
                canonical,
            )
            if (response.status !== 201) throw new DeviceContractError()
            return parsePairingCode(response.data)
        },
        async list(): Promise<OwnerDevice[]> {
            const response = await client.get('/owner/devices')
            if (
                response.status !== 200 ||
                !Array.isArray(response.data) ||
                response.data.length > MAX_DEVICES
            ) {
                throw new DeviceContractError()
            }
            return response.data.map(parseDevice)
        },
        async revoke(deviceId: string): Promise<void> {
            if (!uuid(deviceId)) throw new DeviceContractError()
            const response = await client.delete(`/owner/devices/${deviceId}`)
            if (response.status !== 204 || response.data !== null)
                throw new DeviceContractError()
        },
    })
}

export const devicesApi = createDevicesApi(apiClient)
