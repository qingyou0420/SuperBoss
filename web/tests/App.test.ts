import { render, screen } from '@testing-library/vue'
import ElementPlus from 'element-plus'
import { createPinia } from 'pinia'
import App from '../src/App.vue'
import router from '../src/app/router'

const renderApp = async (path: string) => {
    await router.push(path)
    await router.isReady()

    return render(App, {
        global: {
            plugins: [createPinia(), router, ElementPlus],
        },
    })
}

test('shows the readiness message at /health', async () => {
    await renderApp('/health')

    expect(screen.getByText('SuperBoss is ready')).toBeInTheDocument()
})
