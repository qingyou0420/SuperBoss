import { createHash, randomUUID } from 'node:crypto'

import { expect, test } from '@playwright/test'

import { csrfHeaders, loginThroughLocalAccount } from './support/auth'
import { e2e } from './support/runtime'

test('独立 STAFF 直接请求项目、用户管理、外项目文件与公司财务均被拒绝', async ({
    browser,
    page,
}, testInfo) => {
    const ownerContext = await browser.newContext({
        baseURL: e2e.baseUrl,
        ignoreHTTPSErrors: e2e.ignoreHTTPSErrors,
    })
    let foreignFileId: string
    try {
        const ownerPage = await ownerContext.newPage()
        await loginThroughLocalAccount(ownerPage, 'OWNER', e2e.ownerCredentials)
        const foldersResponse = await ownerPage.request.get('/api/v1/folders')
        expect(foldersResponse.status()).toBe(200)
        const folders = (await foldersResponse.json()) as Array<{
            id: string
            name: string
        }>
        const privateFolder = folders.find((folder) => folder.name === '老板私有')
        expect(privateFolder).toBeDefined()
        if (privateFolder === undefined) {
            throw new Error('Private folder fixture is missing.')
        }
        const fixture = Buffer.from('SuperBoss staff-denial probe')
        const started = await ownerPage.request.post('/api/v1/files/uploads', {
            data: {
                content_type: 'text/plain',
                filename: 'staff-foreign-probe.txt',
                folder_id: privateFolder.id,
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
    console.log('ACCEPTANCE_FOREIGN_FILE_ID=' + foreignFileId)

    await loginThroughLocalAccount(page, 'STAFF', e2e.staffCredentials)
    const headers = await csrfHeaders(page.context())
    const projectCreate = await page.request.post('/api/v1/projects', {
        data: { is_test: true, name: `STAFF forbidden ${randomUUID()}` },
        headers,
    })
    const userList = await page.request.get('/api/v1/owner/users')
    const foreignFile = await page.request.get(
        `/api/v1/files/${foreignFileId}/download`,
    )
    const financeSummary = await page.request.get(
        '/api/v1/finance/summary?month=2026-09',
    )
    const agentList = await page.request.get('/api/v1/agent/conversations')
    const auditList = await page.request.get('/api/v1/audit')
    const financeCreate = await page.request.post('/api/v1/finance/entries', {
        data: {
            amount_cents: 1,
            category: 'forbidden',
            kind: 'COST',
            occurred_on: '2026-09-01',
            scope: 'COMPANY',
        },
        headers,
    })

    expect(projectCreate.status()).toBe(403)
    expect(userList.status()).toBe(403)
    expect(foreignFile.status()).toBe(403)
    expect(financeSummary.status()).toBe(200)
    const summaryBody = (await financeSummary.json()) as {
        company?: unknown
        projects?: Array<Record<string, unknown>>
    }
    expect(JSON.stringify(summaryBody)).not.toMatch(/"INCOME"|"COMPANY"/)
    expect(summaryBody.company).toBeUndefined()
    expect(
        (summaryBody.projects ?? []).some(
            (item) => item.income_cents !== undefined,
        ),
    ).toBe(false)
    expect(financeCreate.status()).toBe(403)
    expect(agentList.status()).toBe(403)
    expect(auditList.status()).toBe(403)
})
