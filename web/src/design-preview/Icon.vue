<script setup lang="ts">
defineOptions({ name: 'PreviewIcon' })
withDefaults(defineProps<{ name: string; size?: number }>(), { size: 19 })
const paths: Record<string, string> = {
    home: 'M3 10 12 3l9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z',
    grid: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
    projects: 'M3 6h7l2 2h9v12H3zM3 6V4h7l2 2',
    map: 'm3 5 6-2 6 2 6-2v16l-6 2-6-2-6 2zM9 3v16M15 5v16',
    pin: 'M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0zM14.5 10a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0',
    book: 'M12 5C8 2 3 4 3 4v15s5-2 9 1c4-3 9-1 9-1V4s-5-2-9 1zM12 5v15',
    chart: 'M4 3v17h17M8 15l4-5 4 2 5-7',
    wallet: 'M3 6h17v14H3zM3 6V4h14v2M16 10h5v6h-5zM18 13h.1',
    coins: 'M19 6c0 2-3 3-7 3S5 8 5 6s3-3 7-3 7 1 7 3zM5 6v6c0 2 3 3 7 3s7-1 7-3V6M5 12v6c0 2 3 3 7 3s7-1 7-3v-6',
    search: 'M16 10a6 6 0 1 1-12 0 6 6 0 0 1 12 0M15 15l6 6',
    bell: 'M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4',
    calendar: 'M4 5h16v16H4zM4 10h16M8 3v4M16 3v4M8 14h2M14 14h2M8 17h2',
    clock: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M12 7v5l3 2',
    file: 'M5 3h9l5 5v13H5zM14 3v6h5M8 13h8M8 17h6',
    image: 'M3 3h18v18H3zM3 17l6-6 4 4 3-3 5 5M16 7h.1',
    upload: 'M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6',
    download: 'M12 3v13m-5-5 5 5 5-5M4 16v5h16v-5',
    'arrow-right': 'M4 12h16m-6-6 6 6-6 6',
    'arrow-left': 'M20 12H4m6-6-6 6 6 6',
    'chevron-right': 'm9 5 7 7-7 7',
    'chevron-down': 'm5 9 7 7 7-7',
    check: 'm5 12 4 4L19 6',
    close: 'm6 6 12 12M18 6 6 18',
    plus: 'M12 4v16M4 12h16',
    more: 'M5 12h.1M12 12h.1M19 12h.1',
    list: 'M8 5h13M8 12h13M8 19h13M3 5h.1M3 12h.1M3 19h.1',
    user: 'M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0M4 21v-2a8 8 0 0 1 16 0v2',
    users: 'M14 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0M2 21v-2a8 8 0 0 1 16 0v2M18 3a4 4 0 0 1 0 8M19 14a6 6 0 0 1 3 5v2',
    phone: 'M7 3H3c-1 9 9 19 18 18v-4l-5-2-2 2c-3-1-6-4-7-7l2-2z',
    building:
        'M4 21V3h13v18M17 9h4v12M8 7h1M12 7h1M8 11h1M12 11h1M8 15h1M12 15h1M9 21v-3h3v3',
    info: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M12 11v6M12 7h.1',
    moon: 'M20 14A8 8 0 0 1 10 4 8.5 8.5 0 1 0 20 14z',
    spark: 'm12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z',
    send: 'm3 3 18 9-18 9 3-9zM6 12h15',
    link: 'm10 13 4-4M8 15l-2 2a4 4 0 0 1-5-5l5-5a4 4 0 0 1 5 0M16 9l2-2a4 4 0 0 1 5 5l-5 5a4 4 0 0 1-5 0',
    settings:
        'M12 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2',
    lock: 'M5 10h14v11H5zM8 10V6a4 4 0 0 1 8 0v4M12 14v3',
    menu: 'M3 6h18M3 12h18M3 18h18',
}
</script>

<template>
    <svg
        :width="size"
        :height="size"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="1.65"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
    >
        <path :d="paths[name] || paths.file" />
    </svg>
</template>
