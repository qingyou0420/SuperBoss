import { spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

import { expect, test } from '@playwright/test'

const FOREIGN_FILE_ID = '8d4d7b0f-0ef6-4ad8-a6bd-8c15fb4b0f39'
const PREFIX = 'ACCEPTANCE_FOREIGN_FILE_ID='

test('a passing list run keeps the labeled safe UUID in captured terminal output after cleanup', () => {
    const root = mkdtempSync(resolve(tmpdir(), 'superboss-staff-evidence-'))
    const output = resolve(root, 'output')
    mkdirSync(output)
    try {
        const require = createRequire(import.meta.url)
        const playwrightTest = pathToFileURL(require.resolve('@playwright/test')).href
        writeFileSync(
            resolve(root, 'synthetic.spec.mjs'),
            `import playwright from ${JSON.stringify(playwrightTest)};\n` +
                `const { test } = playwright;\n` +
                `test('passing staff evidence', () => console.log('${PREFIX}${FOREIGN_FILE_ID}'));\n`,
        )
        writeFileSync(
            resolve(root, 'playwright.config.mjs'),
            `export default {testDir:${JSON.stringify(root)},outputDir:${JSON.stringify(output)},reporter:'list'};\n`,
        )
        const playwrightCli = require.resolve('@playwright/test/cli')
        const run = spawnSync(
            process.execPath,
            [playwrightCli, 'test', '--config', resolve(root, 'playwright.config.mjs')],
            { encoding: 'utf8' },
        )
        expect(run.status, run.stderr || run.stdout).toBe(0)
        const capturedTerminalTranscript = run.stdout
        rmSync(output, { force: true, recursive: true })
        expect(existsSync(output)).toBe(false)
        expect(capturedTerminalTranscript).toMatch(
            new RegExp(`(?:^|\\r?\\n)${PREFIX}${FOREIGN_FILE_ID}(?:\\r?\\n|$)`),
        )

        const liveSpec = readFileSync(
            resolve(import.meta.dirname, '../specs/staff-denial.spec.ts'),
            'utf8',
        )
        expect(liveSpec).toContain("console.log('ACCEPTANCE_FOREIGN_FILE_ID=' + foreignFileId)")
    } finally {
        rmSync(root, { force: true, recursive: true })
    }
})
