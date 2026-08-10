import { expect, type BrowserContext, type Page } from '@playwright/test'

import { e2e } from './runtime'

const APP_COOKIE_NAMES = /^(access_token|refresh_token|XSRF-TOKEN|wecom_oauth_state)$/

export async function csrfHeaders(context: BrowserContext): Promise<Record<string, string>> {
    const cookie = (await context.cookies(e2e.baseUrl)).find(
        (candidate) => candidate.name === 'XSRF-TOKEN',
    )
    if (!cookie?.value) throw new Error('Authenticated state has no XSRF-TOKEN cookie.')
    return { 'X-CSRF-Token': cookie.value }
}

export async function loginThroughWeCom(page: Page, expectedRole: 'OWNER' | 'STAFF'): Promise<void> {
    const appCookies = (await page.context().cookies(e2e.baseUrl)).filter((cookie) =>
        APP_COOKIE_NAMES.test(cookie.name),
    )
    for (const cookie of appCookies) {
        await page.context().clearCookies({
            domain: cookie.domain,
            name: cookie.name,
            path: cookie.path,
        })
    }
    await page.goto('/login')
    await page.getByRole('button', { name: '企业微信登录' }).click()
    await page.waitForURL(
        (url) =>
            url.origin === e2e.baseUrl &&
            (url.pathname === '/owner' || url.pathname === '/forbidden'),
        { timeout: 120_000 },
    )
    const me = await page.request.get('/api/v1/auth/me')
    expect(me.status()).toBe(200)
    const identity = (await me.json()) as { role: string }
    expect(identity.role).toBe(expectedRole)
    if (expectedRole === 'OWNER') {
        await expect(page).toHaveURL(`${e2e.baseUrl}/owner`)
    } else {
        await expect(page).toHaveURL(`${e2e.baseUrl}/forbidden`)
    }
}
