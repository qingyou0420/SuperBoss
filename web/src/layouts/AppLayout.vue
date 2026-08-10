<script setup lang="ts">
import { useRouter } from 'vue-router'

import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()

async function logout(): Promise<void> {
    await auth.logout()
    await router.replace('/login')
}
</script>

<template>
    <div class="app-layout">
        <header class="app-layout__header">
            <router-link to="/owner">SuperBoss</router-link>
            <nav class="app-layout__navigation" aria-label="OWNER 导航">
                <router-link to="/owner/projects">项目</router-link>
                <router-link to="/owner/drive">文件上传</router-link>
                <router-link to="/owner/devices">设备</router-link>
                <router-link to="/owner/import-jobs">导入任务</router-link>
                <router-link to="/owner/users">Users</router-link>
            </nav>
            <div class="app-layout__account">
                <span>{{ auth.user?.userid }}</span>
                <el-button text @click="logout">退出登录</el-button>
            </div>
        </header>
        <main class="app-layout__content">
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
