<script setup lang="ts">
import { computed } from 'vue'

import { homePath } from '../app/navigation'
import { authCopy } from '../copy/pages/auth'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const home = computed(() => homePath(auth.user?.role))
</script>

<template>
    <main class="message-page">
        <h1>{{ authCopy.forbidden }}</h1>
        <router-link :to="home">{{ authCopy.backHome }}</router-link>
    </main>
</template>

<style scoped>
.message-page {
    max-width: 640px;
    padding: 64px 24px;
    margin: 0 auto;
    text-align: center;
}
h1 {
    font-size: var(--sb-xl);
    font-weight: 600;
    margin-bottom: 16px;
}
</style>
