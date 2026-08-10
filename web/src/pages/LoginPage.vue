<script setup lang="ts">
import { inject, ref } from 'vue'
import { routeLocationKey } from 'vue-router'

import { authApi, navigateToAuthorization } from '../api/auth'
import { clearPostLoginPath, rememberPostLoginPath } from '../app/router'
import { useAuthStore } from '../stores/auth'

const props = withDefaults(
    defineProps<{
        navigate?: (target: string) => void
    }>(),
    {
        navigate: (target: string) => globalThis.location.assign(target),
    },
)

const pending = ref(false)
const errorMessage = ref('')
const auth = useAuthStore()
const route = inject(routeLocationKey, undefined)

async function login(): Promise<void> {
    if (pending.value) return
    pending.value = true
    errorMessage.value = ''
    auth.clearError()
    try {
        const started = await authApi.startWeCom()
        if (route) rememberPostLoginPath(route.query.redirect)
        navigateToAuthorization(started.authorization_url, props.navigate)
    } catch {
        if (route) clearPostLoginPath()
        errorMessage.value = '登录暂时不可用，请稍后重试。'
    } finally {
        pending.value = false
    }
}
</script>

<template>
    <main class="login-page">
        <section class="login-card" aria-labelledby="login-title">
            <p class="login-card__eyebrow">SuperBoss</p>
            <h1 id="login-title">登录工作台</h1>
            <p class="login-card__description">
                使用已获授权的企业微信账号继续。
            </p>
            <el-button
                type="primary"
                size="large"
                :loading="pending"
                @click="login"
            >
                企业微信登录
            </el-button>
            <el-alert
                v-if="errorMessage || auth.errorMessage"
                type="error"
                :closable="false"
                show-icon
            >
                {{ errorMessage || auth.errorMessage }}
            </el-alert>
        </section>
    </main>
</template>

<style scoped>
.login-page {
    display: grid;
    min-height: calc(100vh - 64px);
    place-items: center;
    padding: 24px;
    background: #f5f7fa;
}

.login-card {
    display: grid;
    width: min(420px, 100%);
    gap: 18px;
    padding: 36px;
    background: #fff;
    border: 1px solid #e4e7ed;
    border-radius: 12px;
}

.login-card__eyebrow {
    margin: 0;
    color: #409eff;
    font-weight: 700;
}

.login-card h1,
.login-card__description {
    margin: 0;
}

.login-card__description {
    color: #606266;
}
</style>
