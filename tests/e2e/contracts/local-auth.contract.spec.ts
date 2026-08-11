import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { expect, test } from '@playwright/test'

test('live specs use local credentials and have no OAuth callback flow', () => {
    const specsRoot = resolve(import.meta.dirname, '../specs')
    const auth = readFileSync(resolve(specsRoot, 'support/auth.ts'), 'utf8')
    const liveSpecs = [
        'owner-login-project.spec.ts',
        'staff-denial.spec.ts',
        'file-quarantine.spec.ts',
        'device-import.spec.ts',
    ].map((name) => readFileSync(resolve(specsRoot, name), 'utf8'))
    const currentSource = [auth, ...liveSpecs].join('\n').toLowerCase()

    expect(auth).toContain('loginThroughLocalAccount')
    expect(auth).toMatch(/getByLabel\(["']用户名["']\)/)
    expect(auth).toMatch(/getByLabel\(["']密码["']\)/)
    expect(currentSource).not.toContain('wecom')
    expect(currentSource).not.toContain('/auth/callback')
    expect(currentSource).not.toContain('storageState')
})
