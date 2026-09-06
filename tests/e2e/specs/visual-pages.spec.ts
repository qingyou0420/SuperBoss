import { expect, test, type Page } from '@playwright/test'

import { csrfHeaders, loginThroughLocalAccount } from './support/auth'
import { e2e } from './support/runtime'

const SNAPSHOT = {
    animations: 'disabled' as const,
    fullPage: true,
    maxDiffPixelRatio: 0.005,
}

test.describe('六页视觉回归 1280', () => {
    test.use({ viewport: { width: 1280, height: 800 } })

    test('登录', async ({ page }) => {
        await page.goto('/login')
        await expect(page.getByRole('heading', { name: '登录' })).toBeVisible()
        await expect(page).toHaveScreenshot('login.png', SNAPSHOT)
    })

    test('对话 财务 项目详情 网盘 成员', async ({ page }) => {
        await loginThroughLocalAccount(page, 'OWNER', e2e.ownerCredentials)

        await page.goto('/chat')
        await expect(page.getByRole('heading', { name: '霜月' })).toBeVisible()
        await expect(page).toHaveScreenshot('chat.png', {
            ...SNAPSHOT,
            mask: [page.locator('.shell__account')],
        })

        await page.goto('/finance')
        await expect(page.getByRole('heading', { name: '财务' })).toBeVisible()
        await expect(page).toHaveScreenshot('finance.png', {
            ...SNAPSHOT,
            mask: [
                page.locator('.shell__account'),
                page.locator('.month-label'),
            ],
        })

        const projectId = await firstProjectId(page)
        await page.goto(`/projects/${projectId}`)
        await expect(page.locator('#project-detail-title')).toBeVisible()
        await expect(page).toHaveScreenshot('project-detail.png', {
            ...SNAPSHOT,
            mask: [page.locator('.shell__account')],
        })

        await page.goto('/drive')
        await expect(page.getByRole('heading', { name: '网盘' })).toBeVisible()
        await expect(page).toHaveScreenshot('drive.png', {
            ...SNAPSHOT,
            mask: [page.locator('.shell__account')],
        })

        await page.goto('/members')
        await expect(page.getByRole('heading', { name: '成员' })).toBeVisible()
        await expect(page).toHaveScreenshot('members.png', {
            ...SNAPSHOT,
            mask: [page.locator('.shell__account')],
        })
    })
})

interface JsonResponse {
    status: number
    body: unknown
}

function recordId(value: unknown): string | undefined {
    if (typeof value !== 'object' || value === null) return undefined
    const record: Record<string, unknown> = { ...value }
    return typeof record.id === 'string' ? record.id : undefined
}

function projectIdFrom(body: unknown): string | undefined {
    if (!Array.isArray(body) || body.length === 0) return undefined
    const first: unknown = body[0]
    return recordId(first)
}

function createdIdFrom(body: unknown): string | undefined {
    return recordId(body)
}

async function firstProjectId(page: Page): Promise<string> {
    const listed: JsonResponse = await page.evaluate(async () => {
        const response = await fetch('/api/v1/projects', {
            credentials: 'include',
        })
        return {
            status: response.status,
            body: (await response.json()) as unknown,
        }
    })
    expect(listed.status).toBe(200)
    const existing = projectIdFrom(listed.body)
    if (existing) return existing
    const headers = await csrfHeaders(page.context())
    const created: JsonResponse = await page.evaluate(async (csrf) => {
        const response = await fetch('/api/v1/projects', {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json', ...csrf },
            body: JSON.stringify({ name: '视觉回归项目' }),
        })
        return {
            status: response.status,
            body: (await response.json()) as unknown,
        }
    }, headers)
    expect(created.status).toBe(201)
    const createdId = createdIdFrom(created.body)
    if (!createdId) throw new Error('Project create did not return an id.')
    return createdId
}
