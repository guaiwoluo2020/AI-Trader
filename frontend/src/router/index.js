import { createRouter, createWebHistory } from 'vue-router'
import Dashboard from '../views/Dashboard.vue'
import TradeOrders from '../views/TradeOrders.vue'
import Statistics from '../views/Statistics.vue'
import Status from '../views/Status.vue'
import Market from '../views/Market.vue'

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
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router