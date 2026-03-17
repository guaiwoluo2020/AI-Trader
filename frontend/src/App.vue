<template>
  <v-app>
    <v-app-bar app color="primary" dark>
      <v-app-bar-nav-icon @click="drawer = !drawer"></v-app-bar-nav-icon>
      <v-toolbar-title>AITrader</v-toolbar-title>
      <v-spacer></v-spacer>
      <v-btn icon>
        <v-icon>mdi-refresh</v-icon>
      </v-btn>
    </v-app-bar>

    <v-navigation-drawer v-model="drawer" app>
      <v-list>
        <v-list-item
          v-for="item in menuItems"
          :key="item.title"
          :to="item.path"
          link
        >
          <v-list-item-icon>
            <v-icon>{{ item.icon }}</v-icon>
          </v-list-item-icon>
          <v-list-item-content>
            <v-list-item-title>{{ item.title }}</v-list-item-title>
          </v-list-item-content>
        </v-list-item>
      </v-list>
    </v-navigation-drawer>

    <v-main>
      <router-view />
    </v-main>
  </v-app>
</template>

<script>
import { ref } from 'vue'

export default {
  name: 'App',
  setup() {
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

    return {
      drawer,
      menuItems,
    }
  },
}
</script>

<style scoped>
.v-app-bar {
  z-index: 1000;
}
</style>