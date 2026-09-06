<script setup lang="ts">
withDefaults(
    defineProps<{
        title: string
        headingId?: string
    }>(),
    { headingId: undefined },
)
</script>

<template>
    <header class="page-header">
        <h1 :id="headingId">{{ title }}</h1>
        <div class="page-header__actions">
            <slot />
        </div>
    </header>
</template>

<style scoped>
.page-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 32px;
}
.page-header h1 {
    font-size: var(--sb-xl);
    font-weight: 600;
    line-height: 1.2;
    letter-spacing: -0.01em;
}
.page-header__actions {
    display: flex;
    gap: 8px;
    align-items: center;
}
</style>
