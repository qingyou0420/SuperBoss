import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
    plugins: [vue()],
    server: { host: '127.0.0.1', port: 4176, strictPort: true },
    preview: { host: '127.0.0.1', port: 4176, strictPort: true },
    build: {
        outDir: 'design-dist',
        rollupOptions: { input: 'design.html' },
    },
})
