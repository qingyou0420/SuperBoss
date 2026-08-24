import {
    createRouter,
    createWebHistory,
    type Router,
    type RouterHistory,
} from 'vue-router'

import { useAuthStore } from '../stores/auth'
import {
    setAuthenticationLostHandler,
    setSessionRefreshedHandler,
} from '../api/http'
import AppLayout from '../layouts/AppLayout.vue'
import ForbiddenPage from '../pages/ForbiddenPage.vue'
import HealthPage from '../pages/HealthPage.vue'
import LoginPage from '../pages/LoginPage.vue'
import PasswordChangePage from '../pages/PasswordChangePage.vue'
import OwnerHomePage from '../pages/owner/OwnerHomePage.vue'
import OwnerDevicesPage from '../pages/owner/DevicesPage.vue'
import OwnerDrivePage from '../pages/owner/DrivePage.vue'
import OwnerImportJobsPage from '../pages/owner/ImportJobsPage.vue'
import OwnerProjectsPage from '../pages/owner/ProjectsPage.vue'
import OwnerUsersPage from '../pages/owner/UsersPage.vue'

declare module 'vue-router' {
    interface RouteMeta {
        requiresAuth?: boolean
        roles?: Array<'OWNER' | 'STAFF'>
    }
}

const FALLBACK_OWNER_PATH = '/owner'
const objectOriginEnvironmentValue = (
    import.meta as ImportMeta & {
        readonly env?: Readonly<Record<string, unknown>>
    }
).env?.VITE_OBJECT_STORAGE_ORIGIN
const configuredObjectOrigin =
    typeof objectOriginEnvironmentValue === 'string'
        ? objectOriginEnvironmentValue
        : ''

function hasUnsafePathText(value: string): boolean {
    for (let index = 0; index < value.length; index += 1) {
        const code = value.charCodeAt(index)
        if (code <= 31 || code === 127) return true
        if (code >= 0xd800 && code <= 0xdbff) {
            const next = value.charCodeAt(index + 1)
            if (next < 0xdc00 || next > 0xdfff) return true
            index += 1
        } else if (code >= 0xdc00 && code <= 0xdfff) {
            return true
        }
    }
    return false
}

export function safePostLoginPath(value: unknown): string {
    if (
        typeof value !== 'string' ||
        !value.startsWith('/') ||
        value.startsWith('//') ||
        value.includes('\\') ||
        hasUnsafePathText(value)
    ) {
        return FALLBACK_OWNER_PATH
    }
    let parsed: URL
    try {
        parsed = new URL(value, 'https://superboss.invalid')
    } catch {
        return FALLBACK_OWNER_PATH
    }
    let decodedPath = parsed.pathname
    try {
        for (let pass = 0; pass < 2; pass += 1) {
            const next = decodeURIComponent(decodedPath)
            if (next === decodedPath) break
            decodedPath = next
        }
    } catch {
        return FALLBACK_OWNER_PATH
    }
    if (
        parsed.origin !== 'https://superboss.invalid' ||
        !parsed.pathname.startsWith('/') ||
        parsed.pathname.startsWith('//') ||
        decodedPath.includes('\\') ||
        decodedPath.startsWith('//') ||
        /(^|\/)\.{1,2}(\/|$)/.test(decodedPath) ||
        parsed.pathname === '/login' ||
        parsed.pathname === '/auth/callback' ||
        parsed.pathname === '/password/change'
    ) {
        return FALLBACK_OWNER_PATH
    }
    return `${parsed.pathname}${parsed.search}${parsed.hash}`
}

export function createAppRouter(
    history: RouterHistory = createWebHistory(),
): Router {
    const router = createRouter({
        history,
        routes: [
            { path: '/', redirect: '/owner' },
            { path: '/health', name: 'health', component: HealthPage },
            { path: '/login', name: 'login', component: LoginPage },
            {
                path: '/password/change',
                name: 'password-change',
                component: PasswordChangePage,
                meta: { requiresAuth: true },
            },
            { path: '/forbidden', name: 'forbidden', component: ForbiddenPage },
            {
                path: '/owner',
                component: AppLayout,
                meta: { requiresAuth: true, roles: ['OWNER'] },
                children: [
                    { path: '', name: 'owner-home', component: OwnerHomePage },
                    {
                        path: 'projects',
                        name: 'owner-projects',
                        component: OwnerProjectsPage,
                    },
                    {
                        path: 'users',
                        name: 'owner-users',
                        component: OwnerUsersPage,
                    },
                    {
                        path: 'drive',
                        name: 'owner-drive',
                        component: OwnerDrivePage,
                        props: {
                            allowedObjectOrigin: configuredObjectOrigin,
                        },
                    },
                    {
                        path: 'devices',
                        name: 'owner-devices',
                        component: OwnerDevicesPage,
                    },
                    {
                        path: 'import-jobs',
                        name: 'owner-import-jobs',
                        component: OwnerImportJobsPage,
                    },
                ],
            },
        ],
    })

    router.beforeEach(async (to) => {
        if (to.name === 'health') return true
        const auth = useAuthStore()
        await auth.bootstrap()
        if (!auth.isAuthenticated) {
            if (to.name === 'login') return true
            return { name: 'login', query: { redirect: to.fullPath } }
        }
        if (auth.user?.must_change_password) {
            if (to.name === 'password-change') return true
            return {
                name: 'password-change',
                query: {
                    redirect:
                        to.name === 'login'
                            ? safePostLoginPath(to.query.redirect)
                            : safePostLoginPath(to.fullPath),
                },
            }
        }
        if (to.name === 'login' || to.name === 'password-change') {
            return auth.user?.role === 'OWNER'
                ? FALLBACK_OWNER_PATH
                : '/forbidden'
        }
        if (
            to.meta.roles &&
            auth.user &&
            !to.meta.roles.includes(auth.user.role)
        ) {
            return { name: 'forbidden' }
        }
        return true
    })

    setAuthenticationLostHandler(async () => {
        const auth = useAuthStore()
        auth.markAuthenticationLost()
        const current = router.currentRoute.value
        if (current.meta.requiresAuth && current.name !== 'login') {
            await router.replace({
                name: 'login',
                query: { redirect: current.fullPath },
            })
        }
    })

    setSessionRefreshedHandler(async () => {
        const auth = useAuthStore()
        try {
            await auth.refresh()
        } catch (error) {
            const current = router.currentRoute.value
            if (current.meta.requiresAuth && current.name !== 'login') {
                await router.replace({
                    name: 'login',
                    query: { redirect: current.fullPath },
                })
            }
            throw error
        }

        const current = router.currentRoute.value
        if (!auth.user) {
            if (current.meta.requiresAuth && current.name !== 'login') {
                await router.replace({
                    name: 'login',
                    query: { redirect: current.fullPath },
                })
            }
            return
        }
        if (
            auth.user.must_change_password &&
            current.name !== 'password-change'
        ) {
            await router.replace({
                name: 'password-change',
                query: { redirect: safePostLoginPath(current.fullPath) },
            })
            return
        }
        if (
            current.meta.roles &&
            !current.meta.roles.includes(auth.user.role)
        ) {
            await router.replace({ name: 'forbidden' })
        } else if (current.name === 'forbidden' && auth.user.role === 'OWNER') {
            await router.replace({ name: 'owner-home' })
        }
    })

    return router
}

export default createAppRouter()
