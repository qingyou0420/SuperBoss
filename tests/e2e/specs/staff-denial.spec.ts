import { createHash, randomUUID } from 'node:crypto'
import { readFile } from 'node:fs/promises'

import { expect, test } from '@playwright/test'

import { csrfHeaders, loginThroughWeCom } from './support/auth'
import { e2e } from './support/runtime'

test.use({ storageState: e2e.staffStorageStatePath })

test('独立 STAFF 直接请求项目、设备、导入列表与外项目文件均返回 403', async ({
    browser,
    page,
}, testInfo) => {
    const ownerContext = await browser.newContext({
        baseURL: e2e.baseUrl,
        ignoreHTTPSErrors: e2e.ignoreHTTPSErrors,
        storageState: e2e.ownerStorageStatePath,
    })
    let foreignFileId: string
    try {
        const ownerPage = await ownerContext.newPage()
        await loginThroughWeCom(ownerPage, 'OWNER')
        const projectResponse = await ownerPage.request.get('/api/v1/projects')
        expect(projectResponse.status()).toBe(200)
        const projects = (await projectResponse.json()) as Array<{
            id: string
            is_test: boolean
        }>
        const acceptanceProject = projects.find((project) => project.is_test)
        expect(acceptanceProject).toBeDefined()
        if (acceptanceProject === undefined) {
            throw new Error('Acceptance project fixture is missing.')
        }
        const fixture = await readFile(e2e.connectorFixtureDir + '/k3-result.json')
        const started = await ownerPage.request.post('/api/v1/files/uploads', {
            data: {
                category: 'E2E',
                content_type: 'application/json',
                file_date: new Date().toISOString().slice(0, 10),
                filename: 'staff-foreign-probe.json',
                project_id: acceptanceProject.id,
                sha256: createHash('sha256').update(fixture).digest('hex'),
                size_bytes: fixture.byteLength,
            },
            headers: {
                ...(await csrfHeaders(ownerContext)),
                'Idempotency-Key': `e2e-staff-denial-${randomUUID()}`,
            },
        })
        expect(started.status()).toBe(201)
        foreignFileId = ((await started.json()) as { file_id: string }).file_id
    } finally {
        await ownerContext.close()
    }
    testInfo.annotations.push({
        type: 'acceptance.foreign_file_id',
        description: foreignFileId,
    })
    await testInfo.attach('acceptance-foreign-file-id', {
        body: Buffer.from(foreignFileId),
        contentType: 'text/plain',
    })

    await loginThroughWeCom(page, 'STAFF')
    const headers = await csrfHeaders(page.context())
    const projectCreate = await page.request.post('/api/v1/projects', {
        data: { is_test: true, name: `STAFF forbidden ${randomUUID()}` },
        headers,
    })
    const deviceCreate = await page.request.post('/api/v1/owner/devices/pairing-codes', {
        data: { project_ids: [randomUUID()] },
        headers,
    })
    const importList = await page.request.get('/api/v1/owner/import-jobs?limit=1')
    const foreignFile = await page.request.get(
        `/api/v1/files/${foreignFileId}/download`,
    )

    expect(projectCreate.status()).toBe(403)
    expect(deviceCreate.status()).toBe(403)
    expect(importList.status()).toBe(403)
    expect(foreignFile.status()).toBe(403)
})
