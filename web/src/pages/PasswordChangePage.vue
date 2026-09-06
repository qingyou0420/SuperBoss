<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { homePath, safePostLoginPath } from '../app/router'
import InlineError from '../components/ui/InlineError.vue'
import { authCopy } from '../copy/pages/auth'
import { useAuthStore } from '../stores/auth'

const currentPassword = ref('')
const newPassword = ref('')
const confirmation = ref('')
const pending = ref(false)
const errorMessage = ref('')
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

function clearPasswords(): void {
    currentPassword.value = ''
    newPassword.value = ''
    confirmation.value = ''
}

async function changePassword(): Promise<void> {
    if (pending.value) return
    errorMessage.value = ''
    if (newPassword.value !== confirmation.value) {
        errorMessage.value = authCopy.mismatch
        return
    }
    pending.value = true
    try {
        await auth.changePassword({
            current_password: currentPassword.value,
            new_password: newPassword.value,
        })
        clearPasswords()
        await router.replace(
            route.query.redirect
                ? safePostLoginPath(route.query.redirect)
                : homePath(auth.user?.role),
        )
    } catch {
        clearPasswords()
        errorMessage.value = '密码更新失败，请检查当前密码和新密码。'
    } finally {
        pending.value = false
    }
}
</script>

<template>
    <main class="auth-page">
        <p class="brand">SuperBoss</p>
        <section class="auth-panel" aria-labelledby="password-title">
            <h1 id="password-title">{{ authCopy.setPassword }}</h1>
            <form class="auth-form" @submit.prevent="changePassword">
                <label for="current-password">{{
                    authCopy.currentPassword
                }}</label>
                <el-input
                    id="current-password"
                    v-model="currentPassword"
                    type="password"
                    autocomplete="current-password"
                    maxlength="128"
                />
                <label for="new-password">{{ authCopy.newPassword }}</label>
                <el-input
                    id="new-password"
                    v-model="newPassword"
                    type="password"
                    autocomplete="new-password"
                    maxlength="128"
                />
                <label for="confirm-password">{{
                    authCopy.confirmPassword
                }}</label>
                <el-input
                    id="confirm-password"
                    v-model="confirmation"
                    type="password"
                    autocomplete="new-password"
                    maxlength="128"
                />
                <el-button
                    type="primary"
                    native-type="submit"
                    :loading="pending"
                >
                    {{ authCopy.updatePassword }}
                </el-button>
            </form>
            <InlineError :message="errorMessage" />
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
