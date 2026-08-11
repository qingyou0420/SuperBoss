import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'

import { expect, test } from '@playwright/test'

import { loadE2eEnvironment } from '../specs/support/environment'

function validEnvironment(): Record<string, string> {
    const root = mkdtempSync(resolve(tmpdir(), 'superboss-e2e-contract-'))
    const fixture = resolve(root, 'fixture')
    mkdirSync(fixture)
    writeFileSync(resolve(fixture, 'manifest.template.json'), '{}')
    writeFileSync(resolve(fixture, 'k3-result.json'), '{}')
    return {
        E2E_BASE_URL: 'https://127.0.0.1:8443',
        E2E_CONNECTOR_COMMAND_JSON: '["connector-placeholder"]',
        E2E_CONNECTOR_FIXTURE_DIR: fixture,
        E2E_OWNER_USERNAME: 'owner-acceptance',
        E2E_OWNER_PASSWORD: 'owner-password-synthetic',
        E2E_STAFF_USERNAME: 'staff-acceptance',
        E2E_STAFF_PASSWORD: 'staff-password-synthetic',
    }
}

test('missing live deployment origin fails fast instead of skipping', () => {
    const environment = validEnvironment()
    delete environment.E2E_BASE_URL
    expect(() => loadE2eEnvironment(environment)).toThrow(
        /E2E_BASE_URL is required/,
    )
})

test('missing local account credential fails fast without printing its contents', () => {
    const environment = validEnvironment()
    delete environment.E2E_OWNER_PASSWORD
    expect(() => loadE2eEnvironment(environment)).toThrow(
        /E2E_OWNER_PASSWORD is required/,
    )
})

test('local credentials are required without external browser storage state', () => {
    const environment = validEnvironment()
    const loaded = loadE2eEnvironment(environment)

    expect(loaded.ownerCredentials).toEqual({
        username: 'owner-acceptance',
        password: 'owner-password-synthetic',
    })
    expect(loaded.staffCredentials).toEqual({
        username: 'staff-acceptance',
        password: 'staff-password-synthetic',
    })
    expect(loaded).not.toHaveProperty('ownerStorageStatePath')
    expect(loaded).not.toHaveProperty('staffStorageStatePath')
})

test('missing connector fixture fails fast before a live test starts', () => {
    const environment = validEnvironment()
    rmSync(resolve(environment.E2E_CONNECTOR_FIXTURE_DIR, 'k3-result.json'))
    expect(() => loadE2eEnvironment(environment)).toThrow(
        /connector attachment fixture must point to an existing regular file/,
    )
})

test('certificate errors require explicit non-production HTTPS loopback opt-in', () => {
    const environment = validEnvironment()
    environment.E2E_ALLOW_LOCAL_SELF_SIGNED = 'true'
    expect(loadE2eEnvironment(environment).ignoreHTTPSErrors).toBe(true)

    environment.E2E_BASE_URL = 'https://example.invalid'
    expect(() => loadE2eEnvironment(environment)).toThrow(/restricted/)
    environment.E2E_BASE_URL = 'https://127.0.0.1:8443'
    environment.E2E_TARGET = 'production'
    expect(() => loadE2eEnvironment(environment)).toThrow(/restricted/)
})

test('bracketed IPv6 loopback supports the explicit local self-signed opt-in', () => {
    const environment = validEnvironment()
    environment.E2E_BASE_URL = 'https://[::1]:8443'
    environment.E2E_ALLOW_LOCAL_SELF_SIGNED = 'true'

    const loaded = loadE2eEnvironment(environment)
    expect(loaded.baseUrl).toBe('https://[::1]:8443')
    expect(loaded.ignoreHTTPSErrors).toBe(true)
})

test('the frozen app.localhost origin supports the local self-signed opt-in', () => {
    const environment = validEnvironment()
    environment.E2E_BASE_URL = 'https://app.localhost'
    environment.E2E_ALLOW_LOCAL_SELF_SIGNED = 'true'

    const loaded = loadE2eEnvironment(environment)
    expect(loaded.baseUrl).toBe('https://app.localhost')
    expect(loaded.ignoreHTTPSErrors).toBe(true)
})

test('production and non-loopback targets keep certificate verification enabled', () => {
    const environment = validEnvironment()
    environment.E2E_BASE_URL = 'https://example.invalid'
    environment.E2E_TARGET = 'production'
    expect(loadE2eEnvironment(environment).ignoreHTTPSErrors).toBe(false)
})
