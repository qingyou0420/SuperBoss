import { createHash } from 'node:crypto'
import { writeFile } from 'node:fs/promises'

import { expect, test } from '@playwright/test'

import { loginThroughLocalAccount } from './support/auth'
import { fetchDownloadBytes } from './support/download'
import { e2e } from './support/runtime'

test('真实上传先显示扫描中、CLEAN 前拒绝下载、CLEAN 后可下载', async ({
    page,
}, testInfo) => {
    await loginThroughLocalAccount(page, 'OWNER', e2e.ownerCredentials)
    await page.goto('/owner/drive')
    await page.getByLabel('项目').selectOption({ label: '验收测试' })

    const cleanPath = testInfo.outputPath('clean-16MiB.txt')
    const cleanBytes = Buffer.alloc(16 * 1024 * 1024, 0x41)
    const cleanSha256 = createHash('sha256').update(cleanBytes).digest('hex')
    await writeFile(cleanPath, cleanBytes)
    await page.getByLabel('文件', { exact: true }).setInputFiles(cleanPath)

    const completedResponse = page.waitForResponse(
        (response) =>
            response.request().method() === 'POST' &&
            /\/api\/v1\/files\/uploads\/[0-9a-f-]+\/complete$/i.test(
                response.url(),
            ),
    )
    await page.getByRole('button', { name: '开始上传' }).click()
    const completion = await completedResponse
    expect(completion.status()).toBe(200)
    const body = (await completion.json()) as {
        file_id: string
        state: string
    }
    expect(body.state).toMatch(/^(QUARANTINED|SCANNING)$/)
    await expect(page.getByRole('status')).toHaveText('扫描中')

    const denied = await page.request.get(
        `/api/v1/files/${body.file_id}/download`,
    )
    expect(denied.status()).toBe(409)
    await expect
        .poll(
            async () =>
                (
                    await page.request.get(
                        `/api/v1/files/${body.file_id}/download`,
                    )
                ).status(),
            { timeout: e2e.scanTimeoutMs },
        )
        .toBe(200)

    await page.getByRole('button', { name: '检查并获取下载' }).click()
    const download = page.getByRole('link', { name: '下载本次文件' })
    await expect(download).toBeVisible()
    await expect(download).toHaveAttribute('href', /^https:\/\//)
    const signedUrl = await download.getAttribute('href')
    if (signedUrl === null)
        throw new Error('Download link has no signed object URL.')
    const evidence = await fetchDownloadBytes(page, signedUrl)
    expect(evidence.status).toBe(200)
    expect(evidence.byteLength).toBe(cleanBytes.byteLength)
    expect(evidence.sha256).toBe(cleanSha256)
})
