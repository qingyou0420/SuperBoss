import { existsSync, statSync } from 'node:fs'
import { isAbsolute, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const E2E_ROOT = resolve(fileURLToPath(new URL('../..', import.meta.url)))
const REPOSITORY_ROOT = resolve(E2E_ROOT, '../..')
const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '::1'])

export interface E2eEnvironment {
    readonly baseUrl: string
    readonly connectorCommand: readonly string[]
    readonly connectorFixtureDir: string
    readonly ignoreHTTPSErrors: boolean
    readonly ownerStorageStatePath: string
    readonly scanTimeoutMs: number
    readonly staffStorageStatePath: string
    readonly testTimeoutMs: number
}

type EnvironmentSource = Readonly<Record<string, string | undefined>>

function required(source: EnvironmentSource, name: string): string {
    const value = source[name]
    if (!value) {
        throw new Error(`${name} is required; see docs/runbooks/m1-owner-acceptance.md.`)
    }
    return value
}

function regularFile(path: string, label: string): string {
    if (!existsSync(path) || !statSync(path).isFile()) {
        throw new Error(`${label} must point to an existing regular file.`)
    }
    return path
}

function storageStatePath(source: EnvironmentSource, name: string): string {
    const raw = required(source, name)
    if (!isAbsolute(raw)) throw new Error(`${name} must be an absolute path.`)
    const path = resolve(raw)
    const repositoryRelative = relative(REPOSITORY_ROOT, path)
    const insideRepository =
        repositoryRelative !== '' &&
        repositoryRelative !== '..' &&
        !repositoryRelative.startsWith(`..${sep}`) &&
        !isAbsolute(repositoryRelative)
    const outputRelative = relative(resolve(E2E_ROOT, 'output'), path)
    const insideIgnoredOutput =
        outputRelative === '' ||
        (outputRelative !== '..' &&
            !outputRelative.startsWith(`..${sep}`) &&
            !isAbsolute(outputRelative))
    if (insideRepository && !insideIgnoredOutput) {
        throw new Error(`${name} must stay outside the repository or under tests/e2e/output/.`)
    }
    return regularFile(path, name)
}

function connectorCommand(source: EnvironmentSource): readonly string[] {
    const raw = required(source, 'E2E_CONNECTOR_COMMAND_JSON')
    let parsed: unknown
    try {
        parsed = JSON.parse(raw)
    } catch {
        throw new Error('E2E_CONNECTOR_COMMAND_JSON must be a JSON string array.')
    }
    if (
        !Array.isArray(parsed) ||
        parsed.length < 1 ||
        parsed.some(
            (part) =>
                typeof part !== 'string' ||
                !part ||
                [...part].some((character) => character.charCodeAt(0) < 32),
        )
    ) {
        throw new Error('E2E_CONNECTOR_COMMAND_JSON must be a non-empty JSON string array.')
    }
    return Object.freeze(parsed as string[])
}

function boundedMilliseconds(
    source: EnvironmentSource,
    name: string,
    fallback: number,
): number {
    const raw = source[name]
    if (raw === undefined) return fallback
    if (!/^\d+$/.test(raw)) throw new Error(`${name} must be a positive integer.`)
    const parsed = Number(raw)
    if (!Number.isSafeInteger(parsed) || parsed < 1 || parsed > 1_800_000) {
        throw new Error(`${name} must be between 1 and 1800000 milliseconds.`)
    }
    return parsed
}

export function loadE2eEnvironment(source: EnvironmentSource): E2eEnvironment {
    const rawBaseUrl = required(source, 'E2E_BASE_URL')
    let base: URL
    try {
        base = new URL(rawBaseUrl)
    } catch {
        throw new Error('E2E_BASE_URL must be an absolute HTTP(S) origin.')
    }
    if (
        !['http:', 'https:'].includes(base.protocol) ||
        base.username ||
        base.password ||
        base.pathname !== '/' ||
        base.search ||
        base.hash ||
        base.origin !== rawBaseUrl.replace(/\/$/, '')
    ) {
        throw new Error('E2E_BASE_URL must be an exact HTTP(S) origin without credentials.')
    }
    if (base.protocol === 'http:' && !LOOPBACK_HOSTS.has(base.hostname)) {
        throw new Error('Plain HTTP is allowed only for a loopback E2E_BASE_URL.')
    }

    const selfSigned = source.E2E_ALLOW_LOCAL_SELF_SIGNED ?? 'false'
    if (!['true', 'false'].includes(selfSigned)) {
        throw new Error('E2E_ALLOW_LOCAL_SELF_SIGNED must be true or false.')
    }
    const production = source.E2E_TARGET === 'production'
    if (production && base.protocol !== 'https:') {
        throw new Error('Production acceptance requires HTTPS.')
    }
    const ignoreHTTPSErrors =
        selfSigned === 'true' &&
        !production &&
        base.protocol === 'https:' &&
        LOOPBACK_HOSTS.has(base.hostname)
    if (selfSigned === 'true' && !ignoreHTTPSErrors) {
        throw new Error(
            'Self-signed certificate opt-in is restricted to a non-production HTTPS loopback origin.',
        )
    }

    const fixtureDir = resolve(
        source.E2E_CONNECTOR_FIXTURE_DIR ?? resolve(E2E_ROOT, 'fixtures/connector'),
    )
    regularFile(resolve(fixtureDir, 'manifest.template.json'), 'connector manifest fixture')
    regularFile(resolve(fixtureDir, 'k3-result.json'), 'connector attachment fixture')

    const scanTimeoutMs = boundedMilliseconds(source, 'E2E_SCAN_TIMEOUT_MS', 600_000)
    return Object.freeze({
        baseUrl: base.origin,
        connectorCommand: connectorCommand(source),
        connectorFixtureDir: fixtureDir,
        ignoreHTTPSErrors,
        ownerStorageStatePath: storageStatePath(source, 'E2E_OWNER_STORAGE_STATE_PATH'),
        scanTimeoutMs,
        staffStorageStatePath: storageStatePath(source, 'E2E_STAFF_STORAGE_STATE_PATH'),
        testTimeoutMs: boundedMilliseconds(
            source,
            'E2E_TEST_TIMEOUT_MS',
            Math.min(1_800_000, scanTimeoutMs + 300_000),
        ),
    })
}
