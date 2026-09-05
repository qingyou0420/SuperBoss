<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { apiClient } from '../api/http'
import { useAuthStore } from '../stores/auth'
import { homePath } from '../app/router'

const auth = useAuthStore()
const router = useRouter()
const home = computed(() => homePath(auth.user?.role))
const isOwner = computed(() => auth.user?.role === 'OWNER')
const reminders = ref<string[]>([])

async function logout(): Promise<void> {
    await auth.logout()
    await router.replace('/login')
}

onMounted(async () => {
    try {
        const response = await apiClient.get('/projects/reminders')
        if (response.status === 200 && Array.isArray(response.data)) {
            reminders.value = response.data
                .map((item) =>
                    typeof item === 'object' && item && 'message' in item
                        ? String((item as { message: unknown }).message)
                        : '',
                )
                .filter(Boolean)
        }
    } catch {
        reminders.value = []
    }
})
</script>

<template>
    <div class="app-layout">
        <header class="app-layout__header">
            <router-link :to="home">SuperBoss</router-link>
            <nav class="app-layout__navigation" aria-label="工作台导航">
                <router-link v-if="isOwner" to="/chat">霜月</router-link>
                <router-link to="/projects">项目</router-link>
                <router-link to="/finance">财务</router-link>
                <router-link to="/drive">网盘</router-link>
                <router-link to="/knowledge">知识库</router-link>
                <router-link v-if="isOwner" to="/memory">记忆</router-link>
                <router-link v-if="isOwner" to="/soul">SOUL</router-link>
                <router-link v-if="isOwner" to="/audit">审计</router-link>
                <router-link v-if="isOwner" to="/users">账号</router-link>
            </nav>
            <div class="app-layout__account">
                <span>{{
                    auth.user?.display_name || auth.user?.username
                }}</span>
                <el-button text @click="logout">退出登录</el-button>
            </div>
        </header>
        <main class="app-layout__content">
            <el-alert
                v-for="item in reminders"
                :key="item"
                type="warning"
                :closable="false"
                show-icon
                style="margin-bottom: 12px"
            >
                {{ item }}
            </el-alert>
            <router-view />
        </main>
    </div>
</template>

<style scoped>
.app-layout {
    min-height: 100vh;
    background: #f5f7fa;
}

.app-layout__header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 28px;
    background: #fff;
    border-bottom: 1px solid #e4e7ed;
}

.app-layout__account {
    display: flex;
    gap: 12px;
    align-items: center;
}

.app-layout__navigation {
    display: flex;
    gap: 18px;
    align-items: center;
}

.app-layout__navigation a {
    color: #606266;
    text-decoration: none;
}

.app-layout__navigation a.router-link-active {
    color: #409eff;
}

.app-layout__content {
    max-width: 1120px;
    padding: 28px;
    margin: 0 auto;
}

@media (max-width: 760px) {
    .app-layout__header {
        flex-wrap: wrap;
        gap: 12px;
    }

    .app-layout__navigation {
        order: 3;
        width: 100%;
        overflow-x: auto;
    }
}
</style>
