<script setup lang="ts">
defineProps<{
    disabled?: boolean
}>()
const emit = defineEmits<{
    files: [files: File[]]
}>()

function onDrop(event: DragEvent): void {
    event.preventDefault()
    const list = event.dataTransfer?.files
    if (!list?.length) return
    emit('files', [...list])
}
</script>

<template>
    <div class="drop-zone" @dragover.prevent @drop="onDrop">
        <slot />
    </div>
</template>

<style scoped>
.drop-zone {
    min-height: 100%;
}
</style>
