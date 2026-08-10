import { defineConfig } from '@playwright/test'

export default defineConfig({
    testDir: './contracts',
    fullyParallel: false,
    forbidOnly: true,
    retries: 0,
    workers: 1,
    reporter: 'list',
})
