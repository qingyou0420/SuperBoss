import { randomUUID } from 'node:crypto'

import { expect, test } from '@playwright/test'

import { loginThroughLocalAccount } from './support/auth'
import { e2e } from './support/runtime'

test('OWNER 本地登录进入工作台并可创建验收测试项目', async ({ page }) => {
    await loginThroughLocalAccount(page, 'OWNER', e2e.ownerCredentials)
    await page.goto('/projects')
    await expect(
        page.getByRole('heading', { name: '项目管理' }),
    ).toBeVisible()
    const projectName = `验收测试 E2E ${randomUUID()}`
    await page.getByLabel('项目名称').fill(projectName)
    await page.getByText('设为验收测试项目', { exact: true }).click()
    await page.getByRole('button', { name: '创建项目' }).click()

    const projectCard = page
        .locator('.project-row')
        .filter({ hasText: projectName })
    await expect(projectCard).toContainText('验收测试')
})
