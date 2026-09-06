<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { homePath, safePostLoginPath } from '../app/router'
import InlineError from '../components/ui/InlineError.vue'
import { authCopy } from '../copy/pages/auth'
import { useAuthStore } from '../stores/auth'

const username = ref('')
const password = ref('')
const pending = ref(false)
const errorMessage = ref('')
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

async function login(): Promise<void> {
    if (pending.value) return
    pending.value = true
    errorMessage.value = ''
    auth.clearError()
    const target = safePostLoginPath(route.query.redirect)
    try {
        await auth.login({ username: username.value, password: password.value })
        password.value = ''
        if (auth.user?.must_change_password) {
            await router.replace({
                name: 'password-change',
                query: { redirect: target },
            })
        } else if (route.query.redirect) {
            await router.replace(target)
        } else {
            await router.replace(homePath(auth.user?.role))
        }
    } catch {
        password.value = ''
        errorMessage.value = authCopy.loginFailed
    } finally {
        pending.value = false
    }
}
</script>

<template>
    <main class="auth-page">
        <p class="brand">SuperBoss</p>
        <section class="auth-panel" aria-labelledby="login-title">
            <h1 id="login-title">{{ authCopy.login }}</h1>
            <form class="auth-form" @submit.prevent="login">
                <label for="login-username">{{ authCopy.username }}</label>
                <el-input
                    id="login-username"
                    v-model="username"
                    autocomplete="username"
                    maxlength="32"
                />
                <label for="login-password">{{ authCopy.password }}</label>
                <el-input
                    id="login-password"
                    v-model="password"
                    type="password"
                    autocomplete="current-password"
                    maxlength="128"
                />
                <el-button
                    type="primary"
                    native-type="submit"
                    :loading="pending"
                >
                    {{ authCopy.login }}
                </el-button>
            </form>
            <InlineError :message="errorMessage || auth.errorMessage" />
        </section>
    </main>
</template>

<style scoped>
.auth-page {
    display: grid;
    min-height: 100vh;
    place-items: center;
    padding: 24px;
    background: var(--sb-paper);
}
.brand {
    position: absolute;
    top: 24px;
    left: 40px;
    font-size: var(--sb-sm);
    font-weight: 600;
}
.auth-panel {
    display: grid;
    width: min(360px, 100%);
    gap: 24px;
}
.auth-panel h1 {
    font-size: var(--sb-xl);
    font-weight: 600;
    letter-spacing: -0.01em;
}
.auth-form {
    display: grid;
    gap: 12px;
}
</style>
