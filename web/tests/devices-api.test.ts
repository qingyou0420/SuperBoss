import { describe, expect, test, vi } from 'vitest'

const MODULE_PATH = '../src/api/devices'
const DEVICE_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f810'
const PROJECT_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f811'

interface DevicesModule {
    DeviceContractError: new () => Error
    createDevicesApi(client: unknown): {
        createPairingCode(command: unknown): Promise<unknown>
        list(): Promise<unknown[]>
        revoke(deviceId: string): Promise<void>
    }
    deviceErrorMessage(error: unknown): string
}

async function devicesModule(): Promise<DevicesModule> {
    return (await import(/* @vite-ignore */ MODULE_PATH)) as DevicesModule
}

function clientWith(steps: Array<{ data: unknown; status: number }>) {
    const calls: Array<{ data?: unknown; method: string; url: string }> = []
    const take = (method: string, url: string, data?: unknown) => {
        calls.push({ data, method, url })
        const next = steps.shift()
        if (!next) throw new Error('unexpected request')
        return Promise.resolve(Object.freeze(next))
    }
    return {
        calls,
        client: Object.freeze({
            delete: vi.fn((url: string) => take('delete', url)),
            get: vi.fn((url: string) => take('get', url)),
            post: vi.fn((url: string, data?: unknown) =>
                take('post', url, data),
            ),
        }),
    }
}

const activeDevice = {
    id: DEVICE_ID,
    last_used_at: '2026-08-10T03:00:00Z',
    name: 'Kimi-PC',
    paired_at: '2026-08-10T02:00:00Z',
    projects: [{ id: PROJECT_ID, name: '客户方案' }],
    revoked_at: null,
    status: 'ACTIVE',
}

describe('strict OWNER device API', () => {
    test('creates, lists, and revokes through exact browser routes/statuses', async () => {
        const mod = await devicesModule()
        const { calls, client } = clientWith([
            {
                data: {
                    expires_at: '2026-08-10T03:10:00Z',
                    raw_code: 'ABCD-EFGH',
                },
                status: 201,
            },
            { data: [activeDevice], status: 200 },
            { data: null, status: 204 },
        ])
        const api = mod.createDevicesApi(client)

        await expect(
            api.createPairingCode({ project_ids: [PROJECT_ID] }),
        ).resolves.toEqual({
            expires_at: '2026-08-10T03:10:00.000Z',
            raw_code: 'ABCD-EFGH',
        })
        await expect(api.list()).resolves.toEqual([
            {
                ...activeDevice,
                last_used_at: '2026-08-10T03:00:00.000Z',
                paired_at: '2026-08-10T02:00:00.000Z',
            },
        ])
        await expect(api.revoke(DEVICE_ID)).resolves.toBeUndefined()
        expect(calls).toEqual([
            {
                data: { project_ids: [PROJECT_ID] },
                method: 'post',
                url: '/owner/devices/pairing-codes',
            },
            { data: undefined, method: 'get', url: '/owner/devices' },
            {
                data: undefined,
                method: 'delete',
                url: `/owner/devices/${DEVICE_ID}`,
            },
        ])
    })

    test('requires at least one unique project before creating a code', async () => {
        const mod = await devicesModule()
        const { calls, client } = clientWith([])
        const api = mod.createDevicesApi(client)

        for (const project_ids of [
            [],
            [PROJECT_ID, PROJECT_ID],
            ['not-a-uuid'],
        ]) {
            await expect(
                api.createPairingCode({ project_ids }),
            ).rejects.toBeInstanceOf(mod.DeviceContractError)
        }
        expect(calls).toHaveLength(0)
    })

    test.each([
        {
            data: { expires_at: '2026-08-10T03:10:00', raw_code: 'ABCD-EFGH' },
            status: 201,
        },
        {
            data: {
                expires_at: '2026-08-10T03:10:00Z',
                raw_code: 'bad\r\ncode',
            },
            status: 201,
        },
        {
            data: { expires_at: '2026-08-10T03:10:00Z', raw_code: 'ABCD-EFGH' },
            status: 200,
        },
    ])('rejects malformed pairing-code response %#', async (reply) => {
        const mod = await devicesModule()
        await expect(
            mod
                .createDevicesApi(clientWith([reply]).client)
                .createPairingCode({ project_ids: [PROJECT_ID] }),
        ).rejects.toBeInstanceOf(mod.DeviceContractError)
    })

    test('strictly validates bounded device rows and 204 empty revoke', async () => {
        const mod = await devicesModule()
        for (const invalid of [
            [{ ...activeDevice, status: 'UNKNOWN' }],
            [{ ...activeDevice, paired_at: '2026-08-10T02:00:00' }],
            [{ ...activeDevice, projects: [{ id: PROJECT_ID, name: '' }] }],
            { items: [activeDevice] },
        ]) {
            await expect(
                mod
                    .createDevicesApi(
                        clientWith([{ data: invalid, status: 200 }]).client,
                    )
                    .list(),
            ).rejects.toBeInstanceOf(mod.DeviceContractError)
        }
        for (const response of [
            { data: null, status: 200 },
            { data: { ok: true }, status: 204 },
        ]) {
            await expect(
                mod
                    .createDevicesApi(clientWith([response]).client)
                    .revoke(DEVICE_ID),
            ).rejects.toBeInstanceOf(mod.DeviceContractError)
        }
    })

    test('uses one fixed safe public error without raw pairing material', async () => {
        const mod = await devicesModule()
        const error = new Error('pairing ABCD-EFGH postgres sentinel traceback')
        expect(mod.deviceErrorMessage(error)).toBe('设备操作失败，请稍后重试。')
        expect(mod.deviceErrorMessage(error)).not.toMatch(
            /ABCD|sentinel|traceback/i,
        )
    })
})
