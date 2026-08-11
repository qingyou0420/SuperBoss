<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { safePostLoginPath } from '../app/router'
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
        } else {
            await router.replace(target)
        }
    } catch {
        password.value = ''
        errorMessage.value = '用户名或密码错误，请重试。'
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
            <p class="login-card__description">使用本地账号继续。</p>
            <form class="login-form" @submit.prevent="login">
                <label for="login-username">用户名</label>
                <el-input
                    id="login-username"
                    v-model="username"
                    autocomplete="username"
                    maxlength="32"
                />
                <label for="login-password">密码</label>
                <el-input
                    id="login-password"
                    v-model="password"
                    type="password"
                    autocomplete="current-password"
                    maxlength="128"
                />
                <el-button
                    type="primary"
                    size="large"
                    native-type="submit"
                    :loading="pending"
                >
                    登录
                </el-button>
            </form>
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

.login-form {
    display: grid;
    gap: 12px;
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
