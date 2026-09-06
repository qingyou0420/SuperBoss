import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './app/router'
import './styles/tokens.css'
import './styles/element.css'
import './styles/base.css'

createApp(App)
    .use(createPinia())
    .use(router)
    .use(ElementPlus, { locale: zhCn })
    .mount('#app')
