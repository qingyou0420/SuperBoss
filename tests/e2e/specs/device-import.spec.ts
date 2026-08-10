import { expect, test } from '@playwright/test'

import { loginThroughWeCom } from './support/auth'
import { buildConnectorFixture, runConnector } from './support/connector'
import { e2e } from './support/runtime'

test.use({ storageState: e2e.ownerStorageStatePath })

test('OWNER 配对真实 connector 后提交 fixture，导入到 RECEIVED 且不产生 M2 版本', async ({
    page,
}, testInfo) => {
    await loginThroughWeCom(page, 'OWNER')
    const projectsResponse = await page.request.get('/api/v1/projects')
    expect(projectsResponse.status()).toBe(200)
    const projects = (await projectsResponse.json()) as Array<{
        id: string
        is_test: boolean
        name: string
    }>
    const project = projects.find((candidate) => candidate.name === '验收测试')
    expect(project?.is_test).toBe(true)
    if (project === undefined) throw new Error('Acceptance project fixture is missing.')

    await page.goto('/owner/devices')
    await page.getByText('验收测试', { exact: true }).click()
    await page.getByRole('button', { name: '生成配对码' }).click()
    const rawCode = await page.locator('.pairing-code code').innerText()
    expect(rawCode).not.toBe('')
    runConnector(['pair', '--server', e2e.baseUrl, '--code', rawCode, '--name', 'E2E-CONNECTOR'])

    const manifestPath = await buildConnectorFixture(
        e2e.connectorFixtureDir,
        testInfo.outputPath('connector-package'),
        project.id,
    )
    const submitted = runConnector([
        'submit',
        '--server',
        e2e.baseUrl,
        '--manifest',
        manifestPath,
    ])
    const [jobId, initialStatus] = submitted.trim().split(/\s+/)
    expect(jobId).toMatch(/^[0-9a-f-]{36}$/i)
    expect(initialStatus).toMatch(/^(SCANNING|RECEIVED)$/)

    await expect.poll(
        () => {
            const status = runConnector([
                'status',
                '--server',
                e2e.baseUrl,
                '--job-id',
                jobId,
            ])
            return status.trim().split(/\s+/)[1]
        },
        { timeout: e2e.scanTimeoutMs },
    ).toBe('RECEIVED')

    const jobsResponse = await page.request.get('/api/v1/owner/import-jobs?limit=100')
    expect(jobsResponse.status()).toBe(200)
    const jobs = (await jobsResponse.json()) as Array<Record<string, unknown>>
    const job = jobs.find((candidate) => candidate.id === jobId)
    expect(job?.status).toBe('RECEIVED')
    expect(job).not.toHaveProperty('document_id')
    expect(job).not.toHaveProperty('document_version_id')
    expect(job).not.toHaveProperty('versions')

    const m2Boundary = await page.request.get(
        `/api/v1/document-versions?import_job_id=${jobId}`,
    )
    expect(m2Boundary.status()).toBe(404)
})
