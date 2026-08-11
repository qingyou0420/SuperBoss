import type { Page } from '@playwright/test'

export interface DownloadEvidence {
    readonly byteLength: number
    readonly sha256: string
    readonly status: number
}

export async function fetchDownloadBytes(
    page: Page,
    signedUrl: string,
): Promise<DownloadEvidence> {
    return page.evaluate(async (url) => {
        let response: Response
        try {
            response = await fetch(url, { credentials: 'omit', redirect: 'error' })
        } catch {
            throw new Error('Browser fetch of the signed object failed.')
        }
        if (response.status !== 200) {
            throw new Error(`Browser fetch returned HTTP ${response.status}.`)
        }
        const bytes = await response.arrayBuffer()
        const digest = await crypto.subtle.digest('SHA-256', bytes)
        return {
            byteLength: bytes.byteLength,
            sha256: [...new Uint8Array(digest)]
                .map((value) => value.toString(16).padStart(2, '0'))
                .join(''),
            status: response.status,
        }
    }, signedUrl)
}
