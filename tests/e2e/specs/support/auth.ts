import { expect, type BrowserContext, type Page } from '@playwright/test'

import { e2e } from './runtime'
import type { LocalCredentials } from './environment'

const APP_COOKIE_NAMES = /^(access_token|refresh_token|XSRF-TOKEN)$/

export async function csrfHeaders(
    context: BrowserContext,
): Promise<Record<string, string>> {
    const cookie = (await context.cookies(e2e.baseUrl)).find(
        (candidate) => candidate.name === 'XSRF-TOKEN',
    )
    if (!cookie?.value)
        throw new Error('Authenticated state has no XSRF-TOKEN cookie.')
    return { 'X-CSRF-Token': cookie.value }
}

export async function loginThroughLocalAccount(
    page: Page,
    expectedRole: 'OWNER' | 'MANAGER' | 'STAFF',
    credentials: LocalCredentials,
): Promise<void> {
    const appCookies = (await page.context().cookies(e2e.baseUrl)).filter(
        (cookie) => APP_COOKIE_NAMES.test(cookie.name),
    )
    for (const cookie of appCookies) {
        await page.context().clearCookies({
            domain: cookie.domain,
            name: cookie.name,
            path: cookie.path,
        })
    }
    await page.goto('/login')
    await page.getByLabel('用户名').fill(credentials.username)
    await page.getByLabel('密码').fill(credentials.password)
    await page.getByRole('button', { name: '登录' }).click()
    const homePath = expectedRole === 'OWNER' ? '/chat' : '/projects'
    await page.waitForURL(
        (url) => url.origin === e2e.baseUrl && url.pathname === homePath,
        { timeout: 120_000 },
    )
    const me = await page.evaluate(async () => {
        const response = await fetch('/api/v1/auth/me', { credentials: 'include' })
        const body = (await response.json()) as { role?: string }
        return { status: response.status, role: body.role }
    })
    expect(me.status).toBe(200)
    expect(me.role).toBe(expectedRole)
    await expect(page).toHaveURL(`${e2e.baseUrl}${homePath}`)
}
