import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import vuetify from './plugins/vuetify'

import './style.css'

document.documentElement.dataset.build = '2026-08-30.3'

const app = createApp(App)

app.use(router)
app.use(vuetify)

app.mount('#app')
