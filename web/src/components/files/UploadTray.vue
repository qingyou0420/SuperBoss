<script setup lang="ts">
export interface TrayItem {
    name: string
    status: string
}

defineProps<{
    items: TrayItem[]
}>()
</script>

<template>
    <aside v-if="items.length" class="tray">
        <p v-for="item in items" :key="item.name">
            {{ item.name }}
            <span>{{ item.status }}</span>
        </p>
    </aside>
</template>

<style scoped>
.tray {
    position: fixed;
    right: 24px;
    bottom: 24px;
    width: 320px;
    padding: 12px 16px;
    background: var(--sb-surface);
    border: 1px solid var(--sb-line);
    border-radius: var(--sb-radius-lg);
    font-size: var(--sb-sm);
    z-index: 20;
}
p {
    display: flex;
    justify-content: space-between;
    margin: 0 0 8px;
}
p:last-child {
    margin: 0;
}
span {
    color: var(--sb-ink-3);
}
</style>
