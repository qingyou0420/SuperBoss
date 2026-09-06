<script setup lang="ts">
defineProps<{
    label: string
}>()
</script>

<template>
    <div class="key-value">
        <dt>{{ label }}</dt>
        <dd><slot /></dd>
    </div>
</template>

<style scoped>
.key-value {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    align-items: baseline;
}
dt {
    color: var(--sb-ink-2);
    font-size: var(--sb-sm);
}
dd {
    margin: 0;
    text-align: right;
}
</style>
