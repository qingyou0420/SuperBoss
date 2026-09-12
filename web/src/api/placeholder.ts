import { apiClient, type BrowserHttpClient } from './http'
import { isRecord } from './parse'

export interface PlaceholderStatus {
    seeded: boolean
    is_placeholder: boolean
    project_name: string
    note: string
}

export function createPlaceholderApi(client: BrowserHttpClient) {
    return {
        async status(): Promise<PlaceholderStatus> {
            const response = await client.get('/placeholder/status')
            if (response.status !== 200 || !isRecord(response.data)) {
                return {
                    seeded: false,
                    is_placeholder: true,
                    project_name: '',
                    note: '',
                }
            }
            return {
                seeded: Boolean(response.data.seeded),
                is_placeholder: response.data.is_placeholder !== false,
                project_name: String(response.data.project_name || ''),
                note: String(response.data.note || ''),
            }
        },
        async seed(): Promise<PlaceholderStatus> {
            const response = await client.post('/placeholder/seed')
            if (response.status !== 200 || !isRecord(response.data)) {
                throw new Error('placeholder seed failed')
            }
            return {
                seeded: Boolean(response.data.seeded),
                is_placeholder: true,
                project_name: String(response.data.completed_project_id || ''),
                note: String(response.data.note || ''),
            }
        },
    }
}

export const placeholderApi = createPlaceholderApi(apiClient)
