<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { accountNav, homePath, mainNav } from '../app/navigation'
import { shellCopy } from '../copy/pages/shell'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const home = computed(() => homePath(auth.user?.role))
const links = computed(() => mainNav(auth.user?.role))
const account = computed(() => accountNav(auth.user?.role))
const width = computed(() =>
    route.meta.width === 'read' ? 'var(--sb-read)' : 'var(--sb-table)',
)

async function logout(): Promise<void> {
    await auth.logout()
    await router.replace('/login')
}

function onCommand(path: string): void {
    if (path === '__logout__') {
        void logout()
        return
    }
    void router.push(path)
}
</script>

<template>
    <div class="shell">
        <header class="shell__header">
            <router-link class="shell__brand" :to="home">SuperBoss</router-link>
            <nav class="shell__nav" :aria-label="shellCopy.nav">
                <router-link
                    v-for="item in links"
                    :key="item.to"
                    :to="item.to"
                    >{{ item.label }}</router-link
                >
            </nav>
            <el-dropdown trigger="click" @command="onCommand">
                <button type="button" class="shell__account">
                    {{ auth.user?.display_name || auth.user?.username }}
                </button>
                <template #dropdown>
                    <el-dropdown-menu>
                        <el-dropdown-item
                            v-for="item in account"
                            :key="item.to"
                            :command="item.to"
                            >{{ item.label }}</el-dropdown-item
                        >
                        <el-dropdown-item divided command="__logout__">{{
                            shellCopy.logout
                        }}</el-dropdown-item>
                    </el-dropdown-menu>
                </template>
            </el-dropdown>
        </header>
        <main class="shell__main" :style="{ maxWidth: width }">
            <router-view />
        </main>
    </div>
</template>

<style scoped>
.shell {
    min-height: 100vh;
    background: var(--sb-paper);
}
.shell__header {
    display: flex;
    align-items: center;
    gap: 24px;
    padding: 16px 40px;
    border-bottom: 1px solid var(--sb-line);
    background: var(--sb-surface);
}
.shell__brand {
    color: var(--sb-ink);
    font-size: var(--sb-sm);
    font-weight: 600;
}
.shell__nav {
    display: flex;
    flex: 1;
    gap: 18px;
}
.shell__nav a {
    color: var(--sb-ink-2);
}
.shell__nav a.router-link-active {
    color: var(--sb-accent);
    font-weight: 600;
}
.shell__account {
    border: 0;
    background: transparent;
    color: var(--sb-ink);
    cursor: pointer;
}
.shell__main {
    padding: 40px 28px 64px;
    margin: 0 auto;
}
@media (max-width: 760px) {
    .shell__header {
        flex-wrap: wrap;
        padding: 16px 20px;
    }
    .shell__nav {
        order: 3;
        width: 100%;
        overflow-x: auto;
    }
}
</style>
