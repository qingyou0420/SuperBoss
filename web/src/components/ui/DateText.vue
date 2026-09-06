<script setup lang="ts">
import { computed } from 'vue'

import { dateLabel, dateShort, dateTimeShort } from '../../api/parse'

const props = withDefaults(
    defineProps<{
        value: string | null | undefined
        format?: 'long' | 'short' | 'time'
    }>(),
    { format: 'long' },
)

const text = computed(() => {
    if (!props.value) return ''
    if (props.format === 'short') return dateShort(props.value)
    if (props.format === 'time') return dateTimeShort(props.value)
    return dateLabel(props.value)
})
</script>

<template>
    <time v-if="value" :datetime="value">{{ text }}</time>
</template>
