import { execFileSync, spawnSync } from 'node:child_process'
import {
    mkdtempSync,
    mkdirSync,
    readFileSync,
    readdirSync,
    rmSync,
    writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

import { expect, test } from '@playwright/test'

import { SAFE_LIVE_ARTIFACT_OPTIONS } from '../specs/support/artifacts'

const DOM_MARKER = 'PAIR-CODE-SYNTHETIC-MARKER'
const COOKIE_MARKER = 'COOKIE-SYNTHETIC-MARKER'
const temporaryRoots: string[] = []

test.afterEach(() => {
    for (const root of temporaryRoots.splice(0)) rmSync(root, { force: true, recursive: true })
})

function filesBelow(root: string): string[] {
    return readdirSync(root, { recursive: true, withFileTypes: true })
        .filter((entry) => entry.isFile())
        .map((entry) => resolve(entry.parentPath, entry.name))
}

test('live config disables credential-bearing screenshots, traces, and video', () => {
    expect(SAFE_LIVE_ARTIFACT_OPTIONS).toEqual({
        screenshot: 'off',
        trace: 'off',
        video: 'off',
    })
    const liveConfig = readFileSync(resolve(import.meta.dirname, '../playwright.config.ts'), 'utf8')
    expect(liveConfig).toContain('...SAFE_LIVE_ARTIFACT_OPTIONS')
    expect(liveConfig).not.toMatch(/only-on-failure|retain-on-failure/)
})

test('a failed authenticated pairing page leaves no marker-bearing artifact', () => {
    const root = mkdtempSync(resolve(tmpdir(), 'superboss-artifact-contract-'))
    temporaryRoots.push(root)
    const output = resolve(root, 'output')
    mkdirSync(output)
    const modulePath = resolve(root, 'artifact-options.mjs')
    writeFileSync(
        modulePath,
        `export default ${JSON.stringify(SAFE_LIVE_ARTIFACT_OPTIONS)}`,
    )
    const specPath = resolve(root, 'synthetic.spec.mjs')
    const require = createRequire(import.meta.url)
    const playwrightTestUrl = pathToFileURL(require.resolve('@playwright/test')).href
    writeFileSync(
        specPath,
        `import { test, expect } from ${JSON.stringify(playwrightTestUrl)};\n` +
            `test('synthetic failure', async ({ page, context }) => {\n` +
            `  await context.addCookies([{name:'session',value:'${COOKIE_MARKER}',domain:'127.0.0.1',path:'/'}]);\n` +
            `  await page.setContent('<main>${DOM_MARKER}</main>');\n` +
            `  expect(1).toBe(2);\n` +
            `});\n`,
    )
    const configPath = resolve(root, 'playwright.config.mjs')
    writeFileSync(
        configPath,
        `import { defineConfig } from ${JSON.stringify(playwrightTestUrl)};\n` +
            `import use from './artifact-options.mjs';\n` +
            `export default defineConfig({testDir:${JSON.stringify(root)},outputDir:${JSON.stringify(output)},reporter:'null',use});\n`,
    )
    const playwrightCli = execFileSync(
        process.execPath,
        ['-p', "require.resolve('@playwright/test/cli')"],
        { encoding: 'utf8' },
    ).trim()
    const run = spawnSync(process.execPath, [playwrightCli, 'test', '--config', configPath], {
        cwd: dirname(import.meta.dirname),
        encoding: 'utf8',
    })
    expect(run.status).toBe(1)
    for (const path of filesBelow(output)) {
        const content = readFileSync(path)
        expect(content.includes(Buffer.from(DOM_MARKER))).toBe(false)
        expect(content.includes(Buffer.from(COOKIE_MARKER))).toBe(false)
        expect(path).not.toMatch(/\.(png|zip|webm)$/i)
    }
})
