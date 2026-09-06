import { computed, readonly, ref } from 'vue'
import { accountAPI } from '@/api/trading'

const STORAGE_KEY = 'ai-trader-selected-account-id'
const accounts = ref([])
const storage = typeof window === 'undefined' ? null : window.localStorage
const selectedAccountId = ref(Number(storage?.getItem(STORAGE_KEY)) || null)
const loadingAccounts = ref(false)
let initializedSelection = false

function stateMeta(account) {
  if (account.status === 'archived') return { label: '已归档', color: 'grey' }
  if (!account.trading_enabled) return { label: '已暂停', color: 'warning' }
  if (account.account_type === 'paper') {
    return account.active
      ? { label: '模拟运行中', color: 'success' }
      : { label: '模拟引擎就绪', color: 'teal' }
  }
  if (account.active) return { label: '活跃', color: 'success' }
  return { label: '不活跃', color: 'error' }
}

async function loadAccountContext() {
  loadingAccounts.value = true
  try {
    const data = await accountAPI.list()
    accounts.value = (Array.isArray(data.accounts) ? data.accounts : []).filter(item => (
      item.account_type === 'mt5' || item.account_type === 'ibkr' || item.account_type === 'paper'
    ))
    const selectedExists = accounts.value.some(item => item.account_id === selectedAccountId.value)
    // 首次进入应用时优先展示当前用户的活跃账户，避免沿用上次保存的离线账户。
    // 用户完成手动切换后保留选择，不在普通刷新时强制改回活跃账户。
    if (!initializedSelection || !selectedExists) {
      const activeAccount = accounts.value.find(item => (
        item.status === 'active' && (item.account_type === 'paper' || item.active)
      ))
      selectedAccountId.value = (
        activeAccount
        || accounts.value.find(item => item.status === 'active')
        || accounts.value[0]
        || {}
      ).account_id || null
    }
    initializedSelection = true
    if (selectedAccountId.value) {
      storage?.setItem(STORAGE_KEY, String(selectedAccountId.value))
    } else {
      storage?.removeItem(STORAGE_KEY)
    }
  } finally {
    loadingAccounts.value = false
  }
}

function selectAccount(accountId) {
  selectedAccountId.value = Number(accountId) || null
  if (selectedAccountId.value) {
    storage?.setItem(STORAGE_KEY, String(selectedAccountId.value))
  }
}

export function useAccountContext() {
  const selectedAccount = computed(() => (Array.isArray(accounts.value) ? accounts.value : []).find(
    item => item.account_id === selectedAccountId.value
  ) || null)
  const accountOptions = computed(() => (Array.isArray(accounts.value) ? accounts.value : []).map(account => {
    const state = stateMeta(account)
    return {
      value: account.account_id,
      title: account.account_type === 'paper'
        ? `${account.account_name} · 模拟盘 · ${state.label}`
        : `${account.account_name} · ${account.mt5_login || '账号待上报'} · ${state.label}`,
      account,
      state,
    }
  }))
  return {
    accounts: readonly(accounts),
    selectedAccountId,
    selectedAccount,
    accountOptions,
    loadingAccounts: readonly(loadingAccounts),
    loadAccountContext,
    selectAccount,
    stateMeta,
  }
}
