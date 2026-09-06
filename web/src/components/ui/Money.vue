<script setup lang="ts">
import { moneyLabel } from '../../api/parse'

defineProps<{
    cents: number
}>()
</script>

<template>
    <span class="money tabular">{{ moneyLabel(cents) }}</span>
</template>

<style scoped>
.money {
    font-variant-numeric: tabular-nums;
}
</style>
