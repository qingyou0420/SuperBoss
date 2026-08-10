<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { authApi, parseOAuthCallback } from '../api/auth'
import { safePostLoginPath } from '../app/router'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const errorMessage = ref('')

function currentSearch(): string {
    const queryStart = route.fullPath.indexOf('?')
    if (queryStart < 0) return ''
    const fragmentStart = route.fullPath.indexOf('#', queryStart)
    return route.fullPath.slice(
        queryStart,
        fragmentStart < 0 ? undefined : fragmentStart,
    )
}

onMounted(async () => {
    let parsed
    try {
        parsed = parseOAuthCallback(currentSearch())
    } catch {
        errorMessage.value = '登录回调无效，请重新登录。'
        return
    }
    try {
        await authApi.completeWeCom({ code: parsed.code, state: parsed.state })
        await auth.bootstrap(true)
        if (!auth.user) throw new Error('Authentication not established')
        if (auth.user.role !== 'OWNER') {
            await router.replace('/forbidden')
            return
        }
        await router.replace(safePostLoginPath(parsed.redirect))
    } catch {
        errorMessage.value = '登录未完成，请重新登录。'
    }
})
</script>

<template>
    <main class="callback-page" aria-live="polite">
        <el-alert v-if="errorMessage" type="error" :closable="false" show-icon>
            {{ errorMessage }}
        </el-alert>
        <p v-else>正在完成登录…</p>
    </main>
</template>

<style scoped>
.callback-page {
    max-width: 560px;
    padding: 48px 24px;
    margin: 0 auto;
    text-align: center;
}
</style>
