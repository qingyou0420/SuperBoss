<script setup lang="ts">
defineProps<{
    message: string
}>()
</script>

<template>
    <p v-if="message" class="inline-error" role="alert">{{ message }}</p>
</template>

<style scoped>
.inline-error {
    margin: 8px 0 0;
    color: var(--sb-danger);
    font-size: var(--sb-sm);
}
</style>
