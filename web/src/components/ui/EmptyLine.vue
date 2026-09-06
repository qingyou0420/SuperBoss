<script setup lang="ts">
defineProps<{
    message: string
}>()
</script>

<template>
    <p class="empty-line">{{ message }}</p>
</template>

<style scoped>
.empty-line {
    color: var(--sb-ink-3);
    font-size: var(--sb-sm);
}
</style>
