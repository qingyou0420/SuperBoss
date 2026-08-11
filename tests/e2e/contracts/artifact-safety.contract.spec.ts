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
import {
    PAIRING_CODE_REDACTED,
    consumePairingCode,
} from '../specs/support/pairing-code'

const DOM_MARKER = 'PAIR-CODE-SYNTHETIC-MARKER'
const COOKIE_MARKER = 'COOKIE-SYNTHETIC-MARKER'
const temporaryRoots: string[] = []

test.afterEach(() => {
    for (const root of temporaryRoots.splice(0))
        rmSync(root, { force: true, recursive: true })
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
    const liveConfig = readFileSync(
        resolve(import.meta.dirname, '../playwright.config.ts'),
        'utf8',
    )
    expect(liveConfig).toContain('...SAFE_LIVE_ARTIFACT_OPTIONS')
    expect(liveConfig).toContain("reporter: [['list'], ['html'")
    expect(liveConfig).not.toMatch(/only-on-failure|retain-on-failure/)
})

test('the live device flow atomically consumes and redacts the pairing code', () => {
    const liveSpec = readFileSync(
        resolve(import.meta.dirname, '../specs/device-import.spec.ts'),
        'utf8',
    )
    expect(liveSpec).toMatch(
        /consumePairingCode\(\s*page\.locator\(["']\.pairing-code code["']\),?\s*\)/,
    )
    expect(liveSpec).not.toMatch(
        /page\.locator\(["']\.pairing-code code["']\)\.innerText\(\)/,
    )
    const implementation = consumePairingCode.toString()
    expect(implementation.match(/\.evaluate\(/g)).toHaveLength(1)
    expect(implementation.indexOf('textContent')).toBeLessThan(
        implementation.lastIndexOf('return'),
    )
    expect(PAIRING_CODE_REDACTED).not.toContain(DOM_MARKER)
})

test('a failed authenticated pairing page leaves no marker-bearing artifact', () => {
    const root = mkdtempSync(resolve(tmpdir(), 'superboss-artifact-contract-'))
    temporaryRoots.push(root)
    const output = resolve(root, 'output')
    const report = resolve(root, 'report')
    mkdirSync(output)
    const modulePath = resolve(root, 'artifact-options.mjs')
    writeFileSync(
        modulePath,
        `export default ${JSON.stringify(SAFE_LIVE_ARTIFACT_OPTIONS)}`,
    )
    const specPath = resolve(root, 'synthetic.spec.mjs')
    const require = createRequire(import.meta.url)
    const playwrightTestUrl = pathToFileURL(
        require.resolve('@playwright/test'),
    ).href
    const pairingHelperUrl = pathToFileURL(
        resolve(import.meta.dirname, '../specs/support/pairing-code.ts'),
    ).href
    writeFileSync(
        specPath,
        `import playwright from ${JSON.stringify(playwrightTestUrl)};\n` +
            `const { test, expect } = playwright;\n` +
            `import { consumePairingCode } from ${JSON.stringify(pairingHelperUrl)};\n` +
            `test('synthetic failure', async ({ page, context }) => {\n` +
            `  await context.addCookies([{name:'session',value:process.env.SYNTH_COOKIE_MARKER,domain:'127.0.0.1',path:'/'}]);\n` +
            `  await page.setContent('<main><section class="pairing-code"><code>'+process.env.SYNTH_DOM_MARKER+'</code></section></main>');\n` +
            `  const raw = await consumePairingCode(page.locator('.pairing-code code'));\n` +
            `  expect(raw.length).toBeGreaterThan(0);\n` +
            `  await expect(page.getByRole('button', {name:'never present'})).toBeVisible();\n` +
            `});\n`,
    )
    const configPath = resolve(root, 'playwright.config.mjs')
    writeFileSync(
        configPath,
        `import playwright from ${JSON.stringify(playwrightTestUrl)};\n` +
            `const { defineConfig } = playwright;\n` +
            `import use from './artifact-options.mjs';\n` +
            `export default defineConfig({testDir:${JSON.stringify(root)},outputDir:${JSON.stringify(output)},reporter:[['list'],['html',{open:'never',outputFolder:${JSON.stringify(report)}}]],use});\n`,
    )
    const playwrightCli = execFileSync(
        process.execPath,
        ['-p', "require.resolve('@playwright/test/cli')"],
        { encoding: 'utf8' },
    ).trim()
    const run = spawnSync(
        process.execPath,
        [playwrightCli, 'test', '--config', configPath],
        {
            cwd: dirname(import.meta.dirname),
            encoding: 'utf8',
            env: {
                ...process.env,
                SYNTH_COOKIE_MARKER: COOKIE_MARKER,
                SYNTH_DOM_MARKER: DOM_MARKER,
            },
        },
    )
    expect(run.status).toBe(1)
    const failureFiles = filesBelow(output)
    const reportFiles = filesBelow(report)
    expect(failureFiles.some((path) => path.endsWith('error-context.md'))).toBe(
        true,
    )
    expect(failureFiles.some((path) => readFileSync(path).byteLength > 0)).toBe(
        true,
    )
    expect(reportFiles.some((path) => readFileSync(path).byteLength > 0)).toBe(
        true,
    )
    const terminalOutput = run.stdout + run.stderr
    expect(terminalOutput).not.toContain(DOM_MARKER)
    expect(terminalOutput).not.toContain(COOKIE_MARKER)
    for (const path of [...failureFiles, ...reportFiles]) {
        const content = readFileSync(path)
        expect(content.includes(Buffer.from(DOM_MARKER))).toBe(false)
        expect(content.includes(Buffer.from(COOKIE_MARKER))).toBe(false)
        expect(path).not.toMatch(/\.(png|zip|webm)$/i)
    }
})
