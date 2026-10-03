<template>
  <div class="page pa-6">
    <h1 class="text-h5 mb-4">每日盈亏统计</h1>
    <div class="controls d-flex ga-3 align-center mb-4">
      <v-select v-model="accountId" :items="accountItems" item-title="label" item-value="id" label="交易账户" density="compact" hide-details style="max-width:260px" />
      <v-text-field v-model="date" type="date" label="日期" density="compact" hide-details style="max-width:190px" />
      <v-btn color="primary" :loading="loading" @click="load">查询</v-btn>
    </div>
    <v-alert v-if="!rows.length && !loading" type="info" variant="tonal">当天暂无已平仓交易统计</v-alert>
    <v-table v-else density="comfortable"><thead><tr><th>品种</th><th>周期</th><th>SETUP</th><th>策略</th><th>运行状态</th><th>交易笔数</th><th>盈利</th><th>亏损</th><th>单笔盈利最大/最小</th><th>单笔亏损最大/最小</th><th>净盈亏</th></tr></thead>
      <tbody><tr v-for="row in rows" :key="`${row.symbol}-${row.period}-${row.setup_type}-${row.strategy_id}`" :class="pnlRowClass(row)"><td>{{ row.symbol }}</td><td>{{ row.period }}</td><td>{{ row.setup_type }}</td><td>{{ row.strategy_name || row.strategy_id || '未归因策略' }}</td><td>{{ strategyStatus(row.strategy_status) }}</td><td>{{ row.trade_count }}</td><td class="profit">{{ money(row.gross_profit) }}</td><td class="loss">{{ money(row.gross_loss) }}</td><td>{{ money(row.max_profit) }} / {{ money(row.min_profit) }}</td><td>{{ money(row.max_loss) }} / {{ money(row.min_loss) }}</td><td :class="Number(row.net_profit)>=0?'profit':'loss'">{{ money(row.net_profit) }}</td></tr></tbody>
    </v-table>
  </div>
</template>
<script setup>
import { computed, onMounted, ref } from 'vue'
import { accountAPI } from '../api/trading'
const accounts = ref([]); const accountId = ref(null); const rows = ref([]); const loading = ref(false)
const date = ref(new Date(Date.now() - 86400000).toISOString().slice(0, 10))
const accountItems = computed(() => accounts.value.map(a => ({ id: a.id || a.account_id, label: a.name || a.account_name || `账户 ${a.id || a.account_id}` })))
async function loadAccounts() { const data = await accountAPI.list(); accounts.value = data.accounts || data || []; if (!accountId.value && accountItems.value.length) accountId.value = accountItems.value[0].id }
async function load() { if (!accountId.value) return; loading.value = true; try { const data = await accountAPI.getPnlStatistics(accountId.value, date.value); rows.value = data.rows || [] } finally { loading.value = false } }
const money = value => value == null ? '--' : Number(value).toFixed(2)
const strategyStatus = value => ({ active: '运行中', paused: '已暂停', ended: '已结束', pending: '待启动' }[value] || value || '未部署')
const pnlRowClass = row => Number(row.net_profit) > 0 ? 'pnl-profit-row' : Number(row.net_profit) < 0 ? 'pnl-loss-row' : ''
onMounted(async () => { await loadAccounts(); await load() })
</script>
<style scoped>.page{max-width:1500px;margin:auto}.profit{color:#16824b}.loss{color:#c0392b}.pnl-profit-row{background:#effaf2}.pnl-loss-row{background:#fff1f0}.pnl-profit-row:hover{background:#e2f5e8}.pnl-loss-row:hover{background:#ffe4e1}</style>
