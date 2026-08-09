<template>
  <v-app>
    <v-app-bar v-if="showShell" color="primary" dark>
      <v-app-bar-nav-icon
        aria-label="打开导航菜单"
        data-testid="navigation-menu-button"
        @click="toggleDrawer"
      ></v-app-bar-nav-icon>
      <v-toolbar-title>AITrader</v-toolbar-title>
      <v-spacer></v-spacer>
      <AccountSelector v-if="showAccountSelector" class="mr-4" />
      <div class="user-badge">{{ currentUsername }}</div>
      <v-btn variant="text" @click="logout">退出登录</v-btn>
    </v-app-bar>

    <v-navigation-drawer v-if="showShell" v-model="drawer" width="280">
      <v-list v-model:opened="openedGroups" nav density="comfortable">
        <v-list-item
          to="/"
          prepend-icon="mdi-view-dashboard"
          title="仪表盘"
          class="dashboard-link"
          link
          @click="closeDrawer"
        />

        <v-divider class="my-2" />

        <v-list-group
          v-for="group in menuGroups"
          :key="group.value"
          :value="group.value"
        >
          <template #activator="{ props }">
            <v-list-item
              v-bind="props"
              :prepend-icon="group.icon"
              :title="group.title"
              class="menu-group"
            />
          </template>

          <v-list-item
            v-for="item in group.items"
            :key="item.path"
            :to="item.path"
            :prepend-icon="item.icon"
            :title="item.title"
            class="menu-child"
            link
            @click="closeDrawer"
          />
        </v-list-group>
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
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { authState, clearAuthSession } from './auth'
import AccountSelector from './components/AccountSelector.vue'
import { useAccountContext } from './composables/useAccountContext'

export default {
  name: 'App',
  components: { AccountSelector },
  setup() {
    const route = useRoute()
    const router = useRouter()
    const drawer = ref(false)
    const openedGroups = ref([])
    const menuGroups = [
      {
        value: 'trading',
        title: '交易管理',
        icon: 'mdi-swap-horizontal-bold',
        items: [
          { title: '交易账户', path: '/accounts', icon: 'mdi-wallet-bifold-outline' },
          { title: '交易指令', path: '/trades', icon: 'mdi-format-list-bulleted' },
          { title: '信号推荐', path: '/market', icon: 'mdi-lightning-bolt' },
          { title: '仓位管理', path: '/positions', icon: 'mdi-chart-box' },
          { title: '统计数据', path: '/statistics', icon: 'mdi-chart-line' },
        ],
      },
      {
        value: 'research',
        title: '策略与回测',
        icon: 'mdi-flask-outline',
        items: [
          { title: '策略配置', path: '/strategy-settings', icon: 'mdi-tune-variant' },
          { title: '持仓管理', path: '/position-management', icon: 'mdi-shield-edit-outline' },
          { title: '回测数据集', path: '/backtest-datasets', icon: 'mdi-database-clock-outline' },
          { title: '回测任务', path: '/backtests', icon: 'mdi-flask-round-bottom-outline' },
        ],
      },
      {
        value: 'market-info',
        title: '市场资讯',
        icon: 'mdi-chart-timeline-variant-shimmer',
        items: [
          { title: '财经日历', path: '/news', icon: 'mdi-newspaper-variant-outline' },
        ],
      },
      {
        value: 'system',
        title: '系统设置',
        icon: 'mdi-cog-outline',
        items: [
          { title: '连接 MT5', path: '/mt5-setup', icon: 'mdi-connection' },
          { title: '用户配置', path: '/settings', icon: 'mdi-account-cog' },
          { title: '运行日志', path: '/logs', icon: 'mdi-text-box-outline' },
        ],
      },
    ]
    const showShell = computed(() => !route.meta.public)
    const currentUsername = computed(() => authState.user?.username || '未登录')
    const accountRoutes = new Set(['Dashboard', 'TradeOrders', 'Statistics', 'Market', 'Positions'])
    const showAccountSelector = computed(() => accountRoutes.has(route.name))
    const { loadAccountContext } = useAccountContext()

    watch(
      () => [authState.token, route.name],
      ([token]) => {
        if (token && showAccountSelector.value) loadAccountContext()
      },
      { immediate: true }
    )

    watch(
      () => route.path,
      (path) => {
        const activeGroup = menuGroups.find((group) =>
          group.items.some((item) => item.path === path)
        )
        if (activeGroup && !openedGroups.value.includes(activeGroup.value)) {
          openedGroups.value = [...openedGroups.value, activeGroup.value]
        }
      },
      { immediate: true }
    )

    function logout() {
      clearAuthSession()
      drawer.value = false
      router.push('/login')
    }

    function toggleDrawer() {
      drawer.value = !drawer.value
    }

    function closeDrawer() {
      drawer.value = false
    }

    return {
      drawer,
      openedGroups,
      menuGroups,
      showShell,
      currentUsername,
      showAccountSelector,
      toggleDrawer,
      closeDrawer,
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

.dashboard-link {
  font-weight: 600;
}

.menu-group {
  font-weight: 600;
}

.menu-child {
  margin-left: 8px;
  font-size: 0.94rem;
}
</style>
