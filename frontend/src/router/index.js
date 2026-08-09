import { createRouter, createWebHistory } from 'vue-router'
import { isAuthenticated } from '../auth'
import Dashboard from '../views/Dashboard.vue'
import Login from '../views/Login.vue'
import Register from '../views/Register.vue'
import TradeOrders from '../views/TradeOrders.vue'
import Statistics from '../views/Statistics.vue'
import Market from '../views/Market.vue'
import Settings from '../views/Settings.vue'
import StrategySettings from '../views/StrategySettings.vue'
import SystemLog from '../views/SystemLog.vue'
import Positions from '../views/Positions.vue'
import News from '../views/News.vue'
import Mt5Setup from '../views/Mt5Setup.vue'
import BacktestDatasets from '../views/BacktestDatasets.vue'
import BacktestTasks from '../views/BacktestTasks.vue'
import Accounts from '../views/Accounts.vue'
import PositionManagement from '../views/PositionManagement.vue'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: Login,
    meta: { public: true }
  },
  {
    path: '/register',
    name: 'Register',
    component: Register,
    meta: { public: true }
  },
  {
    path: '/',
    name: 'Dashboard',
    component: Dashboard,
    meta: { requiresAuth: true }
  },
  {
    path: '/mt5-setup',
    name: 'Mt5Setup',
    component: Mt5Setup,
    meta: { requiresAuth: true }
  },
  {
    path: '/accounts',
    name: 'Accounts',
    component: Accounts,
    meta: { requiresAuth: true }
  },
  {
    path: '/trades',
    name: 'TradeOrders',
    component: TradeOrders,
    meta: { requiresAuth: true }
  },
  {
    path: '/statistics',
    name: 'Statistics',
    component: Statistics,
    meta: { requiresAuth: true }
  },
  {
    path: '/market',
    name: 'Market',
    component: Market,
    meta: { requiresAuth: true }
  },
  {
    path: '/positions',
    name: 'Positions',
    component: Positions,
    meta: { requiresAuth: true }
  },
  {
    path: '/news',
    name: 'News',
    component: News,
    meta: { requiresAuth: true }
  },
  {
    path: '/strategy-settings',
    name: 'StrategySettings',
    component: StrategySettings,
    meta: { requiresAuth: true }
  },
  {
    path: '/position-management',
    name: 'PositionManagement',
    component: PositionManagement,
    meta: { requiresAuth: true }
  },
  {
    path: '/backtest-datasets',
    name: 'BacktestDatasets',
    component: BacktestDatasets,
    meta: { requiresAuth: true }
  },
  {
    path: '/backtests',
    name: 'BacktestTasks',
    component: BacktestTasks,
    meta: { requiresAuth: true }
  },
  {
    path: '/settings',
    name: 'Settings',
    component: Settings,
    meta: { requiresAuth: true }
  },
  {
    path: '/logs',
    name: 'SystemLog',
    component: SystemLog,
    meta: { requiresAuth: true }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to) => {
  if (to.meta.public && isAuthenticated()) {
    return '/'
  }

  if (to.meta.requiresAuth && !isAuthenticated()) {
    return {
      path: '/login',
      query: { redirect: to.fullPath },
    }
  }

  return true
})

export default router
