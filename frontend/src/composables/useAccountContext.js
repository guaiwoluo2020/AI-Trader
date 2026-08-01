import { computed, readonly, ref } from 'vue'
import { accountAPI } from '@/api/trading'

const STORAGE_KEY = 'ai-trader-selected-account-id'
const accounts = ref([])
const storage = typeof window === 'undefined' ? null : window.localStorage
const selectedAccountId = ref(Number(storage?.getItem(STORAGE_KEY)) || null)
const loadingAccounts = ref(false)

function stateMeta(account) {
  if (account.status === 'archived') return { label: '已归档', color: 'grey' }
  if (!account.trading_enabled) return { label: '已暂停', color: 'warning' }
  if (account.active) return { label: '活跃', color: 'success' }
  return { label: '不活跃', color: 'error' }
}

async function loadAccountContext() {
  loadingAccounts.value = true
  try {
    const data = await accountAPI.list()
    accounts.value = (data.accounts || []).filter(item => item.account_type === 'mt5')
    const selectedExists = accounts.value.some(
      item => item.account_id === selectedAccountId.value
    )
    if (!selectedExists) {
      selectedAccountId.value = (
        accounts.value.find(item => item.active)
        || accounts.value.find(item => item.status === 'active')
        || accounts.value[0]
        || {}
      ).account_id || null
    }
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
  const selectedAccount = computed(() => accounts.value.find(
    item => item.account_id === selectedAccountId.value
  ) || null)
  const accountOptions = computed(() => accounts.value.map(account => {
    const state = stateMeta(account)
    return {
      value: account.account_id,
      title: `${account.account_name} · ${account.mt5_login || '账号待上报'} · ${state.label}`,
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
