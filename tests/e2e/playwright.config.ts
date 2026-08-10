import { defineConfig, devices } from '@playwright/test'

import { e2e } from './specs/support/runtime'

export default defineConfig({
    testDir: './specs',
    fullyParallel: false,
    forbidOnly: true,
    retries: 0,
    workers: 1,
    timeout: e2e.testTimeoutMs,
    expect: { timeout: 15_000 },
    outputDir: './output/test-results',
    reporter: [['list'], ['html', { open: 'never', outputFolder: './output/report' }]],
    use: {
        ...devices['Desktop Chrome'],
        baseURL: e2e.baseUrl,
        ignoreHTTPSErrors: e2e.ignoreHTTPSErrors,
        screenshot: 'only-on-failure',
        trace: 'retain-on-failure',
        video: 'retain-on-failure',
    },
})
