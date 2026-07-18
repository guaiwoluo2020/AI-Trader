<template>
  <v-app>
    <v-app-bar v-if="showShell" color="primary" dark>
      <v-app-bar-nav-icon @click="drawer = !drawer"></v-app-bar-nav-icon>
      <v-toolbar-title>AITrader</v-toolbar-title>
      <v-spacer></v-spacer>
      <div class="user-badge">{{ currentUsername }}</div>
      <v-btn variant="text" @click="logout">退出登录</v-btn>
    </v-app-bar>

    <v-navigation-drawer v-if="showShell" v-model="drawer">
      <v-list>
        <v-list-item
          v-for="item in menuItems"
          :key="item.title"
          :to="item.path"
          :prepend-icon="item.icon"
          :title="item.title"
          link
        >
        </v-list-item>
      </v-list>
    </v-navigation-drawer>

    <v-main>
      <router-view v-slot="{ Component }">
        <component :is="Component" />
      </router-view>
    </v-main>
  </v-app>
</template>

<script>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { authState, clearAuthSession } from './auth'

export default {
  name: 'App',
  setup() {
    const route = useRoute()
    const router = useRouter()
    const drawer = ref(false)
    const menuItems = [
      { title: '仪表板', path: '/', icon: 'mdi-view-dashboard' },
      { title: '交易指令', path: '/trades', icon: 'mdi-format-list-bulleted' },
      { title: '行情分析', path: '/market', icon: 'mdi-chart-candlestick' },
      { title: '仓位管理', path: '/positions', icon: 'mdi-chart-box' },
      { title: '财经日历', path: '/news', icon: 'mdi-newspaper-variant-outline' },
      { title: '统计数据', path: '/statistics', icon: 'mdi-chart-line' },
      { title: '服务状态', path: '/status', icon: 'mdi-information' },
      { title: '系统设置', path: '/settings', icon: 'mdi-cog' },
      { title: '运行日志', path: '/logs', icon: 'mdi-text-box-outline' },
    ]
    const showShell = computed(() => route.path !== '/login')
    const currentUsername = computed(() => authState.user?.username || '未登录')

    function logout() {
      clearAuthSession()
      drawer.value = false
      router.push('/login')
    }

    return {
      drawer,
      menuItems,
      showShell,
      currentUsername,
      logout,
    }
  },
}
</script>

<style scoped>
.v-app-bar {
  z-index: 1000;
}

.user-badge {
  margin-right: 12px;
  font-size: 0.95rem;
  opacity: 0.92;
}
</style>
