import { createRouter, createWebHistory } from 'vue-router'
import { isAuthenticated } from '../auth'
import Dashboard from '../views/Dashboard.vue'
import Login from '../views/Login.vue'
import TradeOrders from '../views/TradeOrders.vue'
import Statistics from '../views/Statistics.vue'
import Status from '../views/Status.vue'
import Market from '../views/Market.vue'
import Settings from '../views/Settings.vue'
import SystemLog from '../views/SystemLog.vue'
import Positions from '../views/Positions.vue'
import News from '../views/News.vue'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: Login,
    meta: { public: true }
  },
  {
    path: '/',
    name: 'Dashboard',
    component: Dashboard,
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
    path: '/status',
    name: 'Status',
    component: Status,
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
