<script setup lang="ts">
defineProps<{
    divided?: boolean
}>()
</script>

<template>
    <ul class="row-list" :class="{ 'row-list--divided': divided }">
        <slot />
    </ul>
</template>

<style scoped>
.row-list {
    list-style: none;
    margin: 0;
    padding: 0;
}
.row-list--divided > :deep(li) {
    display: flex;
    align-items: center;
    min-height: 48px;
    border-bottom: 1px solid var(--sb-line);
    gap: 16px;
}
.row-list--divided > :deep(li:last-child) {
    border-bottom: 0;
}
</style>
