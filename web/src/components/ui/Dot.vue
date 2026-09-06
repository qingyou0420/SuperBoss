<script setup lang="ts">
withDefaults(
    defineProps<{
        tone?: 'ok' | 'warn' | 'danger' | 'muted'
    }>(),
    { tone: 'muted' },
)
</script>

<template>
    <span class="dot" :data-tone="tone" aria-hidden="true" />
</template>

<style scoped>
.dot {
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--sb-ink-3);
    vertical-align: middle;
}
.dot[data-tone='ok'] {
    background: var(--sb-ok);
}
.dot[data-tone='warn'] {
    background: var(--sb-warn);
}
.dot[data-tone='danger'] {
    background: var(--sb-danger);
}
</style>
