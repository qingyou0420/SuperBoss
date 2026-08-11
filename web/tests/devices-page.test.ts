import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import ElementPlus from 'element-plus'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

const PAGE_PATH = '../src/pages/owner/DevicesPage.vue'
const DEVICE_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f810'
const ACTIVE_PROJECT_ID = '019f2b8e-18f0-7f31-9f42-3e6a76b9f811'

const mocks = vi.hoisted(() => ({
    devicesApi: {
        createPairingCode: vi.fn(),
        list: vi.fn(),
        revoke: vi.fn(),
    },
    projectsApi: {
        list: vi.fn(),
    },
}))

vi.mock('../src/api/devices', () => ({
    deviceErrorMessage: () => '设备操作失败，请稍后重试。',
    devicesApi: mocks.devicesApi,
}))
vi.mock('../src/api/projects', () => ({ projectsApi: mocks.projectsApi }))

async function renderPage() {
    const module = await import(/* @vite-ignore */ PAGE_PATH)
    return render(module.default, { global: { plugins: [ElementPlus] } })
}

const activeDevice = {
    id: DEVICE_ID,
    last_used_at: '2026-08-10T03:00:00.000Z',
    name: 'Kimi-PC',
    paired_at: '2026-08-10T02:00:00.000Z',
    projects: [{ id: ACTIVE_PROJECT_ID, name: '客户方案' }],
    revoked_at: null,
    status: 'ACTIVE',
}

beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    sessionStorage.clear()
    mocks.projectsApi.list.mockResolvedValue([
        {
            id: ACTIVE_PROJECT_ID,
            is_test: false,
            name: '客户方案',
            status: 'ACTIVE',
        },
        {
            id: '019f2b8e-18f0-7f31-9f42-3e6a76b9f812',
            is_test: false,
            name: '已归档项目',
            status: 'ARCHIVED',
        },
    ])
    mocks.devicesApi.list.mockResolvedValue([activeDevice])
    mocks.devicesApi.createPairingCode.mockResolvedValue({
        expires_at: '2026-08-10T03:10:00.000Z',
        raw_code: 'ABCD-EFGH',
    })
    mocks.devicesApi.revoke.mockResolvedValue(undefined)
})

afterEach(() => {
    vi.restoreAllMocks()
})

describe('OWNER device management page', () => {
    test('renders every device timestamp explicitly in Asia/Shanghai', async () => {
        const originalTimezone = process.env.TZ
        process.env.TZ = 'UTC'
        mocks.devicesApi.list.mockResolvedValue([
            {
                ...activeDevice,
                revoked_at: '2026-08-10T04:00:00.000Z',
                status: 'REVOKED',
            },
        ])
        try {
            await renderPage()

            expect(
                await screen.findByText('首次配对：2026/8/10 10:00:00'),
            ).toBeInTheDocument()
            expect(
                screen.getByText('最近使用：2026/8/10 11:00:00'),
            ).toBeInTheDocument()
            expect(
                screen.getByText('撤销时间：2026/8/10 12:00:00'),
            ).toBeInTheDocument()

            await fireEvent.click(screen.getByLabelText('客户方案'))
            await fireEvent.click(
                screen.getByRole('button', { name: '生成配对码' }),
            )
            expect(
                await screen.findByText(/2026\/8\/10 11:10:00/),
            ).toBeInTheDocument()
        } finally {
            if (originalTimezone === undefined) delete process.env.TZ
            else process.env.TZ = originalTimezone
        }
    })

    test('renders invalid timestamps as a fixed safe placeholder', async () => {
        mocks.devicesApi.list.mockResolvedValue([
            { ...activeDevice, paired_at: 'not-a-date' },
        ])

        await renderPage()

        expect(await screen.findByText('首次配对：暂无')).toBeInTheDocument()
    })

    test('shows device identity, grants, timestamps, and lifecycle state', async () => {
        await renderPage()

        expect(await screen.findByText('Kimi-PC')).toBeInTheDocument()
        expect(screen.getByText('客户方案')).toBeInTheDocument()
        expect(screen.getByText('已启用')).toBeInTheDocument()
        expect(screen.getAllByText(/2026/)).not.toHaveLength(0)
        expect(screen.getByText(/最近使用/)).toBeInTheDocument()
        expect(mocks.devicesApi.list).toHaveBeenCalledTimes(1)
    })

    test('requires one ACTIVE target project before issuing a pairing code', async () => {
        await renderPage()
        await screen.findByText('Kimi-PC')

        await fireEvent.click(
            screen.getByRole('button', { name: '生成配对码' }),
        )
        expect(mocks.devicesApi.createPairingCode).not.toHaveBeenCalled()
        expect(screen.getByRole('alert')).toHaveTextContent(
            /至少选择一个启用中的项目/,
        )

        expect(screen.queryByLabelText('已归档项目')).not.toBeInTheDocument()
        await fireEvent.click(screen.getByLabelText('客户方案'))
        await fireEvent.click(
            screen.getByRole('button', { name: '生成配对码' }),
        )
        await waitFor(() =>
            expect(mocks.devicesApi.createPairingCode).toHaveBeenCalledWith({
                project_ids: [ACTIVE_PROJECT_ID],
            }),
        )
    })

    test('shows the raw code in one place, never stores/logs it, and cannot reveal it again', async () => {
        const consoleSpy = vi
            .spyOn(console, 'log')
            .mockImplementation(() => undefined)
        await renderPage()
        await screen.findByText('Kimi-PC')
        await fireEvent.click(screen.getByLabelText('客户方案'))
        await fireEvent.click(
            screen.getByRole('button', { name: '生成配对码' }),
        )

        expect(await screen.findByText('ABCD-EFGH')).toBeInTheDocument()
        expect(screen.getAllByText('ABCD-EFGH')).toHaveLength(1)
        expect(screen.getByText(/10 分钟|有效期/)).toBeInTheDocument()
        expect(JSON.stringify(localStorage)).not.toContain('ABCD-EFGH')
        expect(JSON.stringify(sessionStorage)).not.toContain('ABCD-EFGH')
        expect(consoleSpy).not.toHaveBeenCalledWith(
            expect.stringContaining('ABCD-EFGH'),
        )

        await fireEvent.click(
            screen.getByRole('button', { name: '我已安全保存' }),
        )
        expect(screen.queryByText('ABCD-EFGH')).not.toBeInTheDocument()
        expect(
            screen.queryByRole('button', { name: /再次显示/ }),
        ).not.toBeInTheDocument()
    })

    test('expires and removes the one-time code at the server deadline', async () => {
        vi.useFakeTimers()
        vi.setSystemTime(new Date('2026-08-10T03:00:00Z'))
        try {
            await renderPage()
            await vi.advanceTimersByTimeAsync(0)
            await fireEvent.click(screen.getByLabelText('客户方案'))
            await fireEvent.click(
                screen.getByRole('button', { name: '生成配对码' }),
            )
            await vi.advanceTimersByTimeAsync(0)
            expect(screen.getByText('ABCD-EFGH')).toBeInTheDocument()

            await vi.advanceTimersByTimeAsync(10 * 60 * 1000)
            expect(screen.queryByText('ABCD-EFGH')).not.toBeInTheDocument()
            expect(screen.getByRole('alert')).toHaveTextContent(/已过期/)
        } finally {
            vi.useRealTimers()
        }
    })

    test('requires explicit revoke confirmation and honors cancel', async () => {
        const confirm = vi.spyOn(window, 'confirm')
        mocks.devicesApi.list
            .mockResolvedValueOnce([activeDevice])
            .mockResolvedValueOnce([
                {
                    ...activeDevice,
                    revoked_at: '2026-08-10T04:00:00.000Z',
                    status: 'REVOKED',
                },
            ])
        await renderPage()
        await screen.findByText('Kimi-PC')

        confirm.mockReturnValueOnce(false)
        await fireEvent.click(screen.getByRole('button', { name: '撤销设备' }))
        expect(mocks.devicesApi.revoke).not.toHaveBeenCalled()

        confirm.mockReturnValueOnce(true)
        await fireEvent.click(screen.getByRole('button', { name: '撤销设备' }))
        await waitFor(() =>
            expect(mocks.devicesApi.revoke).toHaveBeenCalledWith(DEVICE_ID),
        )
        expect(await screen.findByText('已撤销')).toBeInTheDocument()
    })

    test('reloads the authoritative server revocation timestamp after revoke', async () => {
        const serverRevokedAt = '2025-01-02T03:04:05.000Z'
        mocks.devicesApi.list
            .mockReset()
            .mockResolvedValueOnce([activeDevice])
            .mockResolvedValueOnce([
                {
                    ...activeDevice,
                    revoked_at: serverRevokedAt,
                    status: 'REVOKED',
                },
            ])
        vi.spyOn(window, 'confirm').mockReturnValue(true)
        await renderPage()
        await screen.findByText('Kimi-PC')

        await fireEvent.click(screen.getByRole('button', { name: '撤销设备' }))

        await waitFor(() =>
            expect(mocks.devicesApi.list).toHaveBeenCalledTimes(2),
        )
        expect(await screen.findByText(/撤销时间：.*2025/)).toBeInTheDocument()
        expect(screen.getByText('已撤销')).toBeInTheDocument()
    })

    test('does not leak API/provider details through page errors', async () => {
        mocks.devicesApi.list.mockRejectedValue(
            new Error('raw_code=ABCD-EFGH token=sentinel database traceback'),
        )
        await renderPage()

        expect(
            await screen.findByText('设备列表暂时无法加载，请稍后重试。'),
        ).toBeInTheDocument()
        expect(
            screen.queryByText(/ABCD|sentinel|traceback/i),
        ).not.toBeInTheDocument()
    })
})
