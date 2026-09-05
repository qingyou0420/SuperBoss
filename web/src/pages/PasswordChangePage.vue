<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { homePath, safePostLoginPath } from '../app/router'
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
        errorMessage.value = '两次输入的新密码不一致。'
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
    <main class="password-page">
        <section class="password-card" aria-labelledby="password-title">
            <p class="password-card__eyebrow">首次登录</p>
            <h1 id="password-title">设置新密码</h1>
            <p>继续使用前，请先更换临时密码。</p>
            <form class="password-form" @submit.prevent="changePassword">
                <label for="current-password">当前密码</label>
                <el-input
                    id="current-password"
                    v-model="currentPassword"
                    type="password"
                    autocomplete="current-password"
                    maxlength="128"
                />
                <label for="new-password">新密码</label>
                <el-input
                    id="new-password"
                    v-model="newPassword"
                    type="password"
                    autocomplete="new-password"
                    maxlength="128"
                />
                <label for="confirm-password">确认新密码</label>
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
                    更新密码
                </el-button>
            </form>
            <el-alert
                v-if="errorMessage"
                type="error"
                :closable="false"
                show-icon
            >
                {{ errorMessage }}
            </el-alert>
        </section>
    </main>
</template>

<style scoped>
.password-page {
    display: grid;
    min-height: 100vh;
    place-items: center;
    padding: 24px;
    background: #f5f7fa;
}

.password-card {
    display: grid;
    width: min(460px, 100%);
    gap: 16px;
    padding: 36px;
    background: #fff;
    border: 1px solid #e4e7ed;
    border-radius: 12px;
}

.password-card h1,
.password-card p {
    margin: 0;
}

.password-card__eyebrow {
    color: #409eff;
    font-weight: 700;
}

.password-form {
    display: grid;
    gap: 12px;
}
</style>
