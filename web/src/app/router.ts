import { createRouter, createWebHistory } from 'vue-router'
import HealthPage from '../pages/HealthPage.vue'

export default createRouter({
    history: createWebHistory(),
    routes: [
        {
            path: '/',
            redirect: '/health',
        },
        {
            path: '/health',
            component: HealthPage,
        },
    ],
})
