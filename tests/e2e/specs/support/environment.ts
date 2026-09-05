const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '::1'])

function canonicalHostname(hostname: string): string {
    return hostname.startsWith('[') && hostname.endsWith(']')
        ? hostname.slice(1, -1)
        : hostname
}

function isLoopbackHostname(hostname: string): boolean {
    return LOOPBACK_HOSTS.has(hostname) || hostname.endsWith('.localhost')
}

export interface E2eEnvironment {
    readonly baseUrl: string
    readonly ignoreHTTPSErrors: boolean
    readonly ownerCredentials: LocalCredentials
    readonly scanTimeoutMs: number
    readonly staffCredentials: LocalCredentials
    readonly testTimeoutMs: number
}

type EnvironmentSource = Readonly<Record<string, string | undefined>>

export interface LocalCredentials {
    readonly username: string
    readonly password: string
}

function required(source: EnvironmentSource, name: string): string {
    const value = source[name]
    if (!value) {
        throw new Error(
            `${name} is required; see docs/runbooks/m1-owner-acceptance.md.`,
        )
    }
    return value
}

function credentials(
    source: EnvironmentSource,
    prefix: 'OWNER' | 'STAFF',
): LocalCredentials {
    const username = required(source, `E2E_${prefix}_USERNAME`)
    const password = required(source, `E2E_${prefix}_PASSWORD`)
    if (!/^[a-z][a-z0-9._-]{2,31}$/.test(username)) {
        throw new Error(`E2E_${prefix}_USERNAME is invalid.`)
    }
    const passwordCharacters = [...password]
    if (
        passwordCharacters.length < 12 ||
        passwordCharacters.length > 128 ||
        Buffer.byteLength(password, 'utf8') > 512 ||
        passwordCharacters.some((character) => {
            const code = character.codePointAt(0) ?? 0
            return code <= 31 || code === 127 || (code >= 128 && code <= 159)
        })
    ) {
        throw new Error(`E2E_${prefix}_PASSWORD is invalid.`)
    }
    return Object.freeze({ username, password })
}

function boundedMilliseconds(
    source: EnvironmentSource,
    name: string,
    fallback: number,
): number {
    const raw = source[name]
    if (raw === undefined) return fallback
    if (!/^\d+$/.test(raw))
        throw new Error(`${name} must be a positive integer.`)
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
        throw new Error(
            'E2E_BASE_URL must be an exact HTTP(S) origin without credentials.',
        )
    }
    const hostname = canonicalHostname(base.hostname)
    if (base.protocol === 'http:' && !isLoopbackHostname(hostname)) {
        throw new Error(
            'Plain HTTP is allowed only for a loopback E2E_BASE_URL.',
        )
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
        isLoopbackHostname(hostname)
    if (selfSigned === 'true' && !ignoreHTTPSErrors) {
        throw new Error(
            'Self-signed certificate opt-in is restricted to a non-production HTTPS loopback origin.',
        )
    }

    const scanTimeoutMs = boundedMilliseconds(
        source,
        'E2E_SCAN_TIMEOUT_MS',
        600_000,
    )
    return Object.freeze({
        baseUrl: base.origin,
        ignoreHTTPSErrors,
        ownerCredentials: credentials(source, 'OWNER'),
        scanTimeoutMs,
        staffCredentials: credentials(source, 'STAFF'),
        testTimeoutMs: boundedMilliseconds(
            source,
            'E2E_TEST_TIMEOUT_MS',
            Math.min(1_800_000, scanTimeoutMs + 300_000),
        ),
    })
}
