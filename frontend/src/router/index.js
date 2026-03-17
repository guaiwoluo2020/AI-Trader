import { createRouter, createWebHistory } from 'vue-router'
import Dashboard from '../views/Dashboard.vue'
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
    path: '/',
    name: 'Dashboard',
    component: Dashboard
  },
  {
    path: '/trades',
    name: 'TradeOrders',
    component: TradeOrders
  },
  {
    path: '/statistics',
    name: 'Statistics',
    component: Statistics
  },
  {
    path: '/status',
    name: 'Status',
    component: Status
  },
  {
    path: '/market',
    name: 'Market',
    component: Market
  },
  {
    path: '/positions',
    name: 'Positions',
    component: Positions
  },
  {
    path: '/news',
    name: 'News',
    component: News
  },
  {
    path: '/settings',
    name: 'Settings',
    component: Settings
  },
  {
    path: '/logs',
    name: 'SystemLog',
    component: SystemLog
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router