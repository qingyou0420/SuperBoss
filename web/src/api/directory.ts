import { apiClient, formatRequestError, type BrowserHttpClient } from './http'
import { errorCopy } from '../copy/errors'
import { hasRequiredKeys, isRecord, UUID } from './parse'

export type CommunicationStatus =
    'LEARNING' | 'CONTACTING' | 'COMMISSIONED' | 'PAUSED' | 'DROPPED'

export interface DirectoryEntry {
    id: string
    name: string
    district: string
    street: string
    community: string
    property_company: string
    households: number | null
    floor_area: string
    delivered_on: string | null
    estate_type: string
    manager_name: string
    phone_masked: string
    phone: string | null
    source_filename: string
    source_sheet: string
    source_row: number
    project_id?: string | null
    extra?: Record<string, unknown>
}

export interface DirectoryCommunication {
    id: string
    entry_id: string
    occurred_on: string
    contact_name: string
    contact_role: string
    demand: string
    result: string
    next_step: string
    status: CommunicationStatus
    created_at: string
}

export class DirectoryContractError extends Error {
    constructor() {
        super('Invalid directory data')
        this.name = 'DirectoryContractError'
    }
}

function parseEntry(value: unknown): DirectoryEntry {
    if (
        !isRecord(value) ||
        !hasRequiredKeys(value, [
            'id',
            'name',
            'district',
            'street',
            'community',
            'property_company',
            'households',
            'manager_name',
            'phone_masked',
            'source_filename',
            'source_row',
        ])
    ) {
        throw new DirectoryContractError()
    }
    if (typeof value.id !== 'string' || !UUID.test(value.id)) {
        throw new DirectoryContractError()
    }
    return {
        id: value.id,
        name: String(value.name),
        district: String(value.district ?? ''),
        street: String(value.street ?? ''),
        community: String(value.community ?? ''),
        property_company: String(value.property_company ?? ''),
        households:
            typeof value.households === 'number' ? value.households : null,
        floor_area: String(value.floor_area ?? ''),
        delivered_on:
            typeof value.delivered_on === 'string' ? value.delivered_on : null,
        estate_type: String(value.estate_type ?? ''),
        manager_name: String(value.manager_name ?? ''),
        phone_masked: String(value.phone_masked ?? ''),
        phone: typeof value.phone === 'string' ? value.phone : null,
        source_filename: String(value.source_filename),
        source_sheet: String(value.source_sheet ?? 'Sheet1'),
        source_row: Number(value.source_row),
        project_id:
            typeof value.project_id === 'string' ? value.project_id : null,
        extra: isRecord(value.extra)
            ? (value.extra as Record<string, unknown>)
            : {},
    }
}

export function directoryErrorMessage(error: unknown): string {
    return formatRequestError(errorCopy.generic, error, errorCopy.generic)
}

export function createDirectoryApi(client: BrowserHttpClient) {
    return {
        async facets(): Promise<{
            districts: string[]
            streets: string[]
            streets_by_district: Record<string, string[]>
        }> {
            const response = await client.get('/directory/facets')
            if (response.status !== 200 || !isRecord(response.data)) {
                throw new DirectoryContractError()
            }
            return {
                districts: Array.isArray(response.data.districts)
                    ? response.data.districts.map(String)
                    : [],
                streets: Array.isArray(response.data.streets)
                    ? response.data.streets.map(String)
                    : [],
                streets_by_district: isRecord(response.data.streets_by_district)
                    ? (response.data.streets_by_district as Record<
                          string,
                          string[]
                      >)
                    : {},
            }
        },
        async list(params: {
            q?: string
            district?: string
            street?: string
            offset?: number
            limit?: number
        }): Promise<{ items: DirectoryEntry[]; total: number }> {
            const query: Record<string, string> = {
                offset: String(params.offset ?? 0),
                limit: String(params.limit ?? 50),
            }
            if (params.q) query.q = params.q
            if (params.district) query.district = params.district
            if (params.street) query.street = params.street
            const response = await client.get('/directory', { params: query })
            if (response.status !== 200 || !isRecord(response.data)) {
                throw new DirectoryContractError()
            }
            if (!Array.isArray(response.data.items)) {
                throw new DirectoryContractError()
            }
            return {
                items: response.data.items.map(parseEntry),
                total: Number(response.data.total) || 0,
            }
        },
        async importFile(file: File): Promise<{
            inserted: number
            updated: number
            skipped: number
            conflicts: unknown[]
        }> {
            const body = new FormData()
            body.append('file', file)
            const response = await client.post('/directory/imports', body)
            if (response.status !== 200 || !isRecord(response.data)) {
                throw new DirectoryContractError()
            }
            return {
                inserted: Number(response.data.inserted) || 0,
                updated: Number(response.data.updated) || 0,
                skipped: Number(response.data.skipped) || 0,
                conflicts: Array.isArray(response.data.conflicts)
                    ? response.data.conflicts
                    : [],
            }
        },
        async listConflicts(params?: {
            status?: string
            offset?: number
            limit?: number
        }): Promise<{ items: unknown[]; total: number }> {
            const response = await client.get('/directory/conflicts', {
                params: {
                    status: params?.status ?? 'OPEN',
                    offset: String(params?.offset ?? 0),
                    limit: String(params?.limit ?? 200),
                },
            })
            if (response.status !== 200 || !isRecord(response.data)) {
                throw new DirectoryContractError()
            }
            const items = Array.isArray(response.data.items)
                ? response.data.items
                : Array.isArray(response.data)
                  ? response.data
                  : []
            return {
                items,
                total: Number(response.data.total) || items.length,
            }
        },
        async resolveConflict(
            conflictId: string,
            action: 'link' | 'split',
        ): Promise<unknown> {
            if (!UUID.test(conflictId)) throw new DirectoryContractError()
            const response = await client.post(
                `/directory/conflicts/${conflictId}/resolve`,
                { action },
            )
            if (response.status !== 200) throw new DirectoryContractError()
            return response.data
        },
        async listCommunications(
            entryId: string,
        ): Promise<DirectoryCommunication[]> {
            if (!UUID.test(entryId)) throw new DirectoryContractError()
            const response = await client.get(
                `/directory/${entryId}/communications`,
            )
            if (response.status !== 200 || !Array.isArray(response.data)) {
                throw new DirectoryContractError()
            }
            return response.data as DirectoryCommunication[]
        },
        async addCommunication(
            entryId: string,
            command: {
                occurred_on: string
                contact_name: string
                contact_role: string
                demand: string
                result: string
                next_step?: string
                status?: CommunicationStatus
            },
        ): Promise<DirectoryCommunication> {
            if (!UUID.test(entryId)) throw new DirectoryContractError()
            const response = await client.post(
                `/directory/${entryId}/communications`,
                command,
            )
            if (response.status !== 201) throw new DirectoryContractError()
            return response.data as DirectoryCommunication
        },
        async convertToProject(
            entryId: string,
            command: {
                name?: string
                starts_on?: string | null
                service_fee_cents?: number | null
                new_engagement?: boolean
            } = {},
        ): Promise<{ id: string; name: string }> {
            if (!UUID.test(entryId)) throw new DirectoryContractError()
            const response = await client.post(
                `/directory/${entryId}/convert-project`,
                command,
            )
            if (response.status !== 201 || !isRecord(response.data)) {
                throw new DirectoryContractError()
            }
            return {
                id: String(response.data.id),
                name: String(response.data.name || ''),
            }
        },
    }
}

export const directoryApi = createDirectoryApi(apiClient)
