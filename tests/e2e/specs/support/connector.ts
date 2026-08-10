import { randomUUID } from 'node:crypto'
import { cp, mkdir, readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { spawnSync } from 'node:child_process'

import { e2e } from './runtime'

export function runConnector(arguments_: readonly string[]): string {
    const [executable, ...prefix] = e2e.connectorCommand
    if (executable === undefined) throw new Error('Connector command is not configured.')
    const result = spawnSync(executable, [...prefix, ...arguments_], {
        encoding: 'utf8',
        env: process.env,
        shell: false,
        timeout: e2e.testTimeoutMs,
        windowsHide: true,
    })
    if (result.error || result.status !== 0) {
        const detail = result.stderr.trim()
        throw new Error(
            `Connector command failed with exit ${String(result.status)}${detail ? `: ${detail}` : '.'}`,
        )
    }
    return result.stdout
}

export async function buildConnectorFixture(
    fixtureDir: string,
    outputDir: string,
    projectId: string,
): Promise<string> {
    await mkdir(outputDir, { recursive: true })
    await cp(resolve(fixtureDir, 'k3-result.json'), resolve(outputDir, 'k3-result.json'))
    const template = await readFile(resolve(fixtureDir, 'manifest.template.json'), 'utf8')
    const manifest = template
        .replace('__PROJECT_ID__', projectId)
        .replace('__IDEMPOTENCY_KEY__', `e2e-${randomUUID()}`)
    const manifestPath = resolve(outputDir, 'manifest.json')
    await writeFile(manifestPath, manifest, 'utf8')
    return manifestPath
}
