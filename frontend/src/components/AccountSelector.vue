<template>
  <v-select
    :model-value="selectedAccountId"
    :items="visibleAccountOptions"
    item-title="title"
    item-value="value"
    label="当前查看账户"
    density="compact"
    variant="outlined"
    hide-details
    :loading="loadingAccounts"
    class="account-selector"
    @update:model-value="selectAccount"
  >
    <template #selection="{ item }">
      <template v-if="item?.raw?.account">
        <span class="account-name">{{ item.raw.account.account_name }}</span>
        <v-chip :color="item.raw.state.color" size="x-small" variant="flat" class="ml-2">
          {{ item.raw.state.label }}
        </v-chip>
      </template>
      <span v-else class="account-name">账户加载中</span>
    </template>
    <template #item="{ props, item }">
      <v-list-item v-if="item?.raw?.account" v-bind="props">
        <template #prepend>
          <span class="state-dot" :class="`state-${item.raw.state.color}`" />
        </template>
        <template #subtitle>
          {{ item.raw.account.mt5_server || '服务器待上报' }}
        </template>
      </v-list-item>
    </template>
  </v-select>
</template>

<script setup>
import { computed, watch } from 'vue'
import { useAccountContext } from '@/composables/useAccountContext'

const props = defineProps({
  accountTypes: { type: Array, default: null },
})

const {
  selectedAccountId,
  accountOptions,
  loadingAccounts,
  selectAccount,
} = useAccountContext()

const visibleAccountOptions = computed(() => (
  props.accountTypes?.length
    ? accountOptions.value.filter(item => props.accountTypes.includes(item.account.account_type))
    : accountOptions.value
))

watch(visibleAccountOptions, (options) => {
  if (!options.length || options.some(item => item.value === selectedAccountId.value)) return
  selectAccount(options[0].value)
}, { immediate: true })
</script>

<style scoped>
.account-selector { width: min(420px, 42vw); }
.account-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.state-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.state-success { background: #16a36a; box-shadow: 0 0 0 4px rgb(22 163 106 / 14%); }
.state-warning { background: #d99216; }
.state-error { background: #d64a3a; }
.state-grey { background: #899095; }
@media (max-width: 700px) { .account-selector { width: 48vw; } }
</style>
