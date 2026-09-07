<template>
  <v-container fluid class="replay-page">
    <header class="page-header">
      <div><div class="section-kicker">STRATEGY EXECUTION REPLAY</div><h1>K线交易回放</h1><p>{{ strategyName }} · {{ deployment?.account_name || '加载部署中' }}</p></div>
      <div class="header-actions"><v-text-field v-model="startAt" type="datetime-local" label="开始时间" density="compact" hide-details style="max-width:190px"/><v-text-field v-model="endAt" type="datetime-local" label="结束时间" density="compact" hide-details style="max-width:190px"/><v-btn v-if="deployment" color="primary" variant="tonal" prepend-icon="mdi-magnify" :loading="loading" @click="load">查询</v-btn><v-btn variant="outlined" prepend-icon="mdi-arrow-left" :to="{ path: '/market', query: { strategy_id: strategyId } }">返回策略执行中心</v-btn></div>
    </header>
    <v-alert v-if="loading" type="info" variant="tonal">正在加载 K线与交易执行事件。</v-alert>
    <v-alert v-else-if="error" type="error" variant="tonal">{{ error }}</v-alert>
    <v-alert v-else-if="!deployment" type="warning" variant="tonal">未找到该策略部署。</v-alert>
    <StrategyExecutionChart v-else :symbol="deployment.chart?.symbol || deployment.symbol" :period="deployment.chart?.period || 'M5'" :bars="deployment.chart?.bars || []" :events="deployment.chart?.events || []" :decisions="deployment.decisions || []"/>
  </v-container>
</template>
<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { marketAPI } from '@/api/market'
import StrategyExecutionChart from '@/components/StrategyExecutionChart.vue'
const route=useRoute(),loading=ref(false),error=ref(''),overview=ref(null),startAt=ref(''),endAt=ref('')
const strategyId=computed(()=>String(route.query.strategy_id||'')),deploymentId=computed(()=>String(route.query.deployment_id||''))
const deployment=computed(()=> (Array.isArray(overview.value?.deployments)?overview.value.deployments:[]).find(item=>String(item?.deployment_id)===deploymentId.value)||null)
const strategyName=computed(()=>overview.value?.strategy?.strategy_name||strategyId.value||'策略')
const load=async()=>{if(!strategyId.value){error.value='缺少 strategy_id';return}loading.value=true;error.value='';try{const toTs=value=>value?Math.floor(new Date(value).getTime()/1000):null;overview.value=await marketAPI.getStrategyExecutionOverview(strategyId.value,{include_chart:true,include_inactive:true,start_ts:toTs(startAt.value),end_ts:toTs(endAt.value)})}catch(e){error.value=e?.response?.data?.detail||'加载策略执行回放失败'}finally{loading.value=false}}
onMounted(load);watch(strategyId,load)
</script>
<style scoped>
.replay-page{max-width:1440px;margin:0 auto;padding-top:24px}.page-header{display:flex;justify-content:space-between;align-items:center;gap:18px;margin-bottom:18px}.header-actions{display:flex;gap:10px;align-items:center}.section-kicker{color:#b18443;font-size:.64rem;font-weight:800;letter-spacing:.12em}.page-header h1{margin:4px 0;color:#29483f;font-size:1.55rem}.page-header p{margin:0;color:#71817a;font-size:.82rem}.review-card{border:1px solid #dbe8e1;border-radius:16px;background:linear-gradient(145deg,#fff,#f7fbf8)}.review-summary{font-size:1.02rem;color:#29483f}.review-card h3{margin:12px 0 8px;color:#315f50;font-size:1rem}.review-item{margin:8px 0;padding:11px 13px;border:1px solid #e0ebe5;border-radius:10px;background:#fbfdfb;line-height:1.55}.review-item.suggestion{border-left:3px solid #2f8063}.evidence{color:#71817a;font-size:.83rem}.risk-notes{margin-top:16px;padding:10px 12px;border-radius:9px;background:#fff8e8;color:#785b20}.review-footnote{margin:16px 0 0;color:#81918b;font-size:.8rem}@media(max-width:700px){.page-header{align-items:flex-start;flex-direction:column}.header-actions{width:100%;flex-wrap:wrap}.header-actions .v-btn{flex:1}}
</style>
