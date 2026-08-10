import {
    createRouter,
    createWebHistory,
    type Router,
    type RouterHistory,
} from 'vue-router'

import { useAuthStore } from '../stores/auth'
import { setAuthenticationLostHandler } from '../api/http'
import AppLayout from '../layouts/AppLayout.vue'
import AuthCallbackPage from '../pages/AuthCallbackPage.vue'
import ForbiddenPage from '../pages/ForbiddenPage.vue'
import HealthPage from '../pages/HealthPage.vue'
import LoginPage from '../pages/LoginPage.vue'
import OwnerHomePage from '../pages/owner/OwnerHomePage.vue'
import OwnerProjectsPage from '../pages/owner/ProjectsPage.vue'

declare module 'vue-router' {
    interface RouteMeta {
        requiresAuth?: boolean
        roles?: Array<'OWNER' | 'STAFF'>
    }
}

const FALLBACK_OWNER_PATH = '/owner'

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
    let decodedPath: string
    try {
        decodedPath = decodeURIComponent(parsed.pathname)
    } catch {
        return FALLBACK_OWNER_PATH
    }
    if (
        parsed.origin !== 'https://superboss.invalid' ||
        decodedPath.includes('\\') ||
        parsed.pathname === '/login' ||
        parsed.pathname === '/auth/callback'
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
            { path: '/', redirect: '/health' },
            { path: '/health', name: 'health', component: HealthPage },
            { path: '/login', name: 'login', component: LoginPage },
            {
                path: '/auth/callback',
                name: 'auth-callback',
                component: AuthCallbackPage,
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
                ],
            },
        ],
    })

    router.beforeEach(async (to) => {
        if (
            to.name === 'auth-callback' ||
            to.name === 'health' ||
            to.name === 'forbidden'
        ) {
            return true
        }
        const auth = useAuthStore()
        await auth.bootstrap()
        if (to.name === 'login') {
            if (!auth.isAuthenticated) return true
            return auth.user?.role === 'OWNER'
                ? FALLBACK_OWNER_PATH
                : '/forbidden'
        }
        if (to.meta.requiresAuth && !auth.isAuthenticated) {
            return { name: 'login', query: { redirect: to.fullPath } }
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

    return router
}

export default createAppRouter()
