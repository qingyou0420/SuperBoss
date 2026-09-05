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
import type { UserRole } from '../api/auth'
import AppLayout from '../layouts/AppLayout.vue'
import ForbiddenPage from '../pages/ForbiddenPage.vue'
import HealthPage from '../pages/HealthPage.vue'
import LoginPage from '../pages/LoginPage.vue'
import PasswordChangePage from '../pages/PasswordChangePage.vue'
import ProjectDetailPage from '../pages/ProjectDetailPage.vue'
import AuditPage from '../pages/AuditPage.vue'
import ChatPage from '../pages/ChatPage.vue'
import FinancePage from '../pages/FinancePage.vue'
import KnowledgePage from '../pages/KnowledgePage.vue'
import MemoryPage from '../pages/MemoryPage.vue'
import SoulPage from '../pages/SoulPage.vue'
import DrivePage from '../pages/owner/DrivePage.vue'
import ProjectsPage from '../pages/owner/ProjectsPage.vue'
import UsersPage from '../pages/owner/UsersPage.vue'

export type AppRole = UserRole

declare module 'vue-router' {
    interface RouteMeta {
        requiresAuth?: boolean
        roles?: AppRole[]
    }
}

const ALL_ROLES: AppRole[] = ['OWNER', 'MANAGER', 'STAFF']
const FALLBACK_PATH = '/projects'
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

export function homePath(role: AppRole | undefined): string {
    if (role === 'OWNER') return '/chat'
    return FALLBACK_PATH
}

export function safePostLoginPath(value: unknown): string {
    if (
        typeof value !== 'string' ||
        !value.startsWith('/') ||
        value.startsWith('//') ||
        value.includes('\\') ||
        hasUnsafePathText(value)
    ) {
        return FALLBACK_PATH
    }
    let parsed: URL
    try {
        parsed = new URL(value, 'https://superboss.invalid')
    } catch {
        return FALLBACK_PATH
    }
    let decodedPath = parsed.pathname
    try {
        for (let pass = 0; pass < 2; pass += 1) {
            const next = decodeURIComponent(decodedPath)
            if (next === decodedPath) break
            decodedPath = next
        }
    } catch {
        return FALLBACK_PATH
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
        return FALLBACK_PATH
    }
    return `${parsed.pathname}${parsed.search}${parsed.hash}`
}

export function createAppRouter(
    history: RouterHistory = createWebHistory(),
): Router {
    const router = createRouter({
        history,
        routes: [
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
                path: '/',
                component: AppLayout,
                meta: { requiresAuth: true, roles: ALL_ROLES },
                children: [
                    { path: '', redirect: FALLBACK_PATH },
                    {
                        path: 'chat',
                        name: 'chat',
                        component: ChatPage,
                        meta: { roles: ['OWNER'] },
                        props: {
                            allowedObjectOrigin: configuredObjectOrigin,
                        },
                    },
                    {
                        path: 'soul',
                        name: 'soul',
                        component: SoulPage,
                        meta: { roles: ['OWNER'] },
                    },
                    {
                        path: 'memory',
                        name: 'memory',
                        component: MemoryPage,
                        meta: { roles: ['OWNER'] },
                    },
                    {
                        path: 'projects',
                        name: 'projects',
                        component: ProjectsPage,
                    },
                    {
                        path: 'projects/:projectId',
                        name: 'project-detail',
                        component: ProjectDetailPage,
                    },
                    {
                        path: 'drive',
                        name: 'drive',
                        component: DrivePage,
                        props: {
                            allowedObjectOrigin: configuredObjectOrigin,
                        },
                    },
                    {
                        path: 'finance',
                        name: 'finance',
                        component: FinancePage,
                    },
                    {
                        path: 'knowledge',
                        name: 'knowledge',
                        component: KnowledgePage,
                    },
                    {
                        path: 'audit',
                        name: 'audit',
                        component: AuditPage,
                        meta: { roles: ['OWNER'] },
                    },
                    {
                        path: 'users',
                        name: 'users',
                        component: UsersPage,
                        meta: { roles: ['OWNER'] },
                    },
                ],
            },
            {
                path: '/owner',
                redirect: (to) => ({
                    path: FALLBACK_PATH,
                    query: to.query,
                    hash: to.hash,
                }),
            },
            {
                path: '/owner/projects',
                redirect: (to) => ({
                    path: '/projects',
                    query: to.query,
                    hash: to.hash,
                }),
            },
            {
                path: '/owner/drive',
                redirect: (to) => ({
                    path: '/drive',
                    query: to.query,
                    hash: to.hash,
                }),
            },
            {
                path: '/owner/users',
                redirect: (to) => ({
                    path: '/users',
                    query: to.query,
                    hash: to.hash,
                }),
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
            return homePath(auth.user?.role)
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
        } else if (current.name === 'forbidden') {
            await router.replace(homePath(auth.user.role))
        }
    })

    return router
}

export default createAppRouter()
