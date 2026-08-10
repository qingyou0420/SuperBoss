import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { authApi, type AuthUser } from '../api/auth'
import { ApiContractError, responseStatus } from '../api/http'

export const useAuthStore = defineStore('auth', () => {
    const user = ref<AuthUser | null>(null)
    const isBootstrapped = ref(false)
    const errorMessage = ref('')
    let bootstrapPromise: Promise<void> | null = null
    let refreshPromise: Promise<void> | null = null

    const isAuthenticated = computed(() => user.value !== null)

    const clearError = (): void => {
        errorMessage.value = ''
    }

    const markAuthenticationLost = (): void => {
        user.value = null
        isBootstrapped.value = true
    }

    const bootstrap = (force = false): Promise<void> => {
        if (bootstrapPromise) return bootstrapPromise
        if (isBootstrapped.value && !force) return Promise.resolve()
        const pending = (async () => {
            errorMessage.value = ''
            try {
                user.value = await authApi.me()
            } catch (error) {
                user.value = null
                if (responseStatus(error) !== 401) {
                    errorMessage.value = ApiContractError.safeMessage(error)
                }
            } finally {
                isBootstrapped.value = true
            }
        })()
        bootstrapPromise = pending.finally(() => {
            bootstrapPromise = null
        })
        return bootstrapPromise
    }

    const refresh = (): Promise<void> => {
        if (refreshPromise) return refreshPromise
        const pending = (async () => {
            errorMessage.value = ''
            try {
                user.value = await authApi.me()
            } catch (error) {
                user.value = null
                if (responseStatus(error) !== 401) {
                    errorMessage.value = ApiContractError.safeMessage(error)
                }
                throw error
            } finally {
                isBootstrapped.value = true
            }
        })()
        refreshPromise = pending.finally(() => {
            refreshPromise = null
        })
        return refreshPromise
    }

    const logout = async (): Promise<void> => {
        errorMessage.value = ''
        try {
            await authApi.logout()
        } catch {
            errorMessage.value = '退出请求未完成，本机已退出。'
        } finally {
            markAuthenticationLost()
        }
    }

    return {
        bootstrap,
        clearError,
        errorMessage,
        isAuthenticated,
        isBootstrapped,
        logout,
        markAuthenticationLost,
        refresh,
        user,
    }
})
