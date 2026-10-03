<template>
  <v-container fluid class="structure-page">
    <section class="hero">
      <div><span>MARKET STRUCTURE</span><h1>{{ symbol }} · {{ period }} 结构分析</h1><p>按 Internal / Swing / External 分卡查看同一套四层结构：形态、阶段、事件、计划。</p></div>
      <div class="controls"><v-select v-model="symbol" :items="symbols" label="品种" density="compact" hide-details variant="outlined"/><v-select v-model="period" :items="periods" label="周期" density="compact" hide-details variant="outlined"/><v-btn color="primary" :loading="loading" @click="load">刷新</v-btn></div>
    </section>
    <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
    <v-alert v-if="structureResult" type="info" variant="tonal" density="compact" class="mb-4"><strong>主结构：</strong>{{ primaryStructureLabel(structureResult.primary_structure) }}。主结构由 Swing 与 External 汇总；每个选项卡只看该层自己的形态、阶段、事件和计划。</v-alert>

    <v-row v-if="eventDecisions.length || eventMatrix.length" class="mb-4">
      <v-col cols="12" md="7">
        <v-card class="decision-card h-100">
          <v-card-title class="d-flex align-center justify-space-between"><span>事件决策</span><v-chip size="x-small" color="primary" variant="tonal">事件 → 动作 → 计划</v-chip></v-card-title>
          <v-card-text>
            <div v-if="eventDecisions.length" class="decision-list">
              <article v-for="item in eventDecisions.slice(-8).reverse()" :key="item.decision_id" class="decision-item">
                <div><v-chip size="x-small" :color="item.status==='accepted'?'success':'warning'" variant="tonal">{{ item.status==='accepted'?'已接受':'已拒绝' }}</v-chip><strong>{{ layerLabel(item.event_layer) }} · {{ eventLabel(item.event_type) }}</strong><span>{{ item.direction==='up'?'向上':item.direction==='down'?'向下':'--' }}</span></div>
                <p>{{ item.reason }}</p><small>{{ item.matrix_action || 'ignore' }} · {{ item.plan_type || item.setup_type || '--' }}<span v-if="item.required_confirmation"> · 后续 {{ item.required_confirmation }}</span></small>
              </article>
            </div>
            <div v-else class="empty">当前没有事件决策</div>
            <div v-if="observationPlans.length" class="observation-summary"><span>观察计划 {{ observationPlans.length }} 个</span><span v-for="item in observationPlans.slice(-3)" :key="item.observation_plan_id">{{ item.event_chain?.join(' → ') }} · {{ item.status }}</span></div>
          </v-card-text>
        </v-card>
      </v-col>
      <v-col cols="12" md="5">
        <v-card class="decision-card h-100">
          <v-card-title>当前确认规则</v-card-title>
          <v-card-text>
            <div class="matrix-summary"><span>矩阵规则</span><strong>{{ eventMatrix.length }} 条</strong></div>
            <div class="matrix-summary"><span>确认 K 线</span><strong>当前周期 · 3 根</strong></div>
            <div class="matrix-summary"><span>多头确认</span><strong>收盘守位 · 低点抬高</strong></div>
            <div class="matrix-summary"><span>空头确认</span><strong>收盘守位 · 高点降低</strong></div>
            <p class="matrix-help">确认序列包含第一根 RETEST 或 RECLAIM K 线。INTERNAL 事件可以创建计划，但不是 SWING 回测的必选条件。</p>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-tabs v-model="activeLayer" color="primary" class="mb-4" show-arrows>
      <v-tab value="internal">Internal</v-tab>
      <v-tab value="swing">Swing</v-tab>
      <v-tab value="external">External</v-tab>
    </v-tabs>

    <v-window v-model="activeLayer">
      <v-window-item v-for="layer in layerKeys" :key="layer" :value="layer">
        <v-row class="mb-4 align-stretch">
          <v-col cols="12" md="7">
            <div class="stacked-panels">
              <v-card class="summary">
                <v-card-title class="d-flex align-center justify-space-between flex-wrap ga-2">
                  <span>{{ hierarchyLabels[layer] }}</span>
                  <v-chip size="small" :color="patternColor(layerState(layer).pattern, layerState(layer).bias)" variant="tonal">{{ patternLabel(layerState(layer).pattern, layerState(layer).bias, layerState(layer).pattern_detail) }}</v-chip>
                </v-card-title>
                <v-card-text>
                  <div class="state-row"><span>Pattern</span><strong>{{ patternLabel(layerState(layer).pattern, layerState(layer).bias, layerState(layer).pattern_detail) }}</strong></div>
                  <div class="state-row"><span>Phase</span><strong>{{ patternPhaseLabel(layerState(layer).pattern, layerState(layer).pattern_phase || layerState(layer).phase) }}</strong></div>
                  <div class="state-row"><span>Event</span><v-chip size="x-small" :color="eventColor(layerState(layer).event)" variant="tonal">{{ eventLabel(layerState(layer).event) }}</v-chip></div>
                  <p v-if="patternDetail(layerState(layer).pattern_detail)">{{ patternDetail(layerState(layer).pattern_detail) }}</p>
                  <div class="stats flex-wrap">
                    <span>当前结构段 {{ layerState(layer).segment?.bars || layerState(layer).pattern_detail?.segment_bars || 0 }} 根</span>
                    <span>{{ layerState(layer).pivot_count || 0 }} 个 Pivot</span>
                    <span>{{ setupMappingLabel(layerState(layer)) }}</span>
                    <span v-if="levelPrice(layerState(layer).protected_high)">保护高点 {{ formatPlanPrice(levelPrice(layerState(layer).protected_high)) }}</span>
                    <span v-if="levelPrice(layerState(layer).protected_low)">保护低点 {{ formatPlanPrice(levelPrice(layerState(layer).protected_low)) }}</span>
                  </div>
                </v-card-text>
              </v-card>
              <v-card class="plan-card">
                <v-card-title>{{ hierarchyLabels[layer] }} · 交易计划</v-card-title>
                <v-card-text>
                  <div v-if="layerPlans(layer).length" class="plan-grid compact-plans">
                    <article v-for="plan in layerPlans(layer)" :key="plan.plan_id">
                      <div class="card-head"><v-chip size="x-small" :color="plan.direction==='buy'?'success':plan.direction==='sell'?'error':'info'" variant="tonal">{{ plan.direction==='buy'?'买入':plan.direction==='sell'?'卖出':'观察' }}</v-chip><strong>{{ planLabel(plan) }}</strong><span>{{ planConsumptionLabel(plan) }}</span></div>
                      <div class="plan-values"><span>入场 {{ formatPlanPrice(plan.entry_price) }}</span><span>止损 {{ formatPlanPrice(plan.stop_loss) }}</span><span>止盈 {{ formatPlanPrice(plan.take_profit) }}</span></div>
                      <div class="plan-chain" v-if="(plan.event_chain||[]).length">{{ (plan.event_chain||[]).join(' → ') }}</div>
                      <div class="plan-confirm">确认 {{ plan.confirmation_bars_seen || 0 }} / {{ plan.confirmation_bars_required || 3 }} · {{ plan.required_confirmation || 'confirmation_sequence' }}</div>
                      <p>{{ plan.reason || '结构条件尚未满足' }}</p>
                    </article>
                  </div>
                  <div v-else class="empty">当前层级没有交易计划</div>
                </v-card-text>
              </v-card>
            </div>
          </v-col>
          <v-col cols="12" md="5">
            <v-card class="summary h-100">
              <v-card-title class="d-flex align-center justify-space-between flex-wrap ga-2">
                <span>{{ hierarchyLabels[layer] }} · 结构事件</span>
                <div class="event-filters">
                  <v-chip size="x-small" :color="eventFilter==='structure'?'primary':'default'" :variant="eventFilter==='structure'?'tonal':'outlined'" @click="eventFilter='structure'">BOS / CHoCH</v-chip>
                  <v-chip size="x-small" :color="eventFilter==='sweep'?'primary':'default'" :variant="eventFilter==='sweep'?'tonal':'outlined'" @click="eventFilter='sweep'">扫单</v-chip>
                  <v-chip size="x-small" :color="eventFilter==='all'?'primary':'default'" :variant="eventFilter==='all'?'tonal':'outlined'" @click="eventFilter='all'">全部</v-chip>
                </div>
              </v-card-title>
              <v-card-text>
                <div v-if="layerEvents(layer).length" class="event-stack compact-events tall-events">
                  <article v-for="item in layerEvents(layer)" :key="item.key" class="event-item" :class="item.direction==='up'?'event-up':'event-down'">
                    <div class="event-main">
                      <v-chip size="x-small" :color="eventColor(item.type)" variant="tonal">{{ eventLabel(item.type) }}</v-chip>
                      <strong>{{ item.direction==='up'?'上':'下' }}</strong>
                      <span>{{ formatEventLevel(item.level) }}</span>
                      <span class="event-time">{{ barStamp(item.index) }}</span>
                      <span v-if="item.count>1" class="event-count">×{{ item.count }}</span>
                    </div>
                  </article>
                </div>
                <div v-else class="empty">当前层级没有结构事件</div>
              </v-card-text>
            </v-card>
          </v-col>
        </v-row>

        <v-card v-if="bars.length" class="chart-card mb-4">
          <v-card-title>{{ hierarchyLabels[layer] }} · K线与结构段</v-card-title>
          <v-card-text>
            <div :ref="el => setChartRef(layer, el)" class="chart" style="height:480px;width:100%"></div>
            <div class="legend"><span v-for="type in ['up','sideways','triangle','down','transition']" :key="type"><i :style="{background:legendColors[type]}"></i>{{ labels[type] }}</span></div>
          </v-card-text>
        </v-card>

        <v-card v-if="layerSegments(layer).length" class="mb-4">
          <v-card-title>{{ hierarchyLabels[layer] }} · 最近结构段</v-card-title>
          <v-card-text>
            <div class="segment-grid">
              <article v-for="item in layerSegments(layer)" :key="`${layer}-${item.id}`" :class="{active:item.id===layerSegments(layer).at(-1)?.id}">
                <div class="card-head"><v-chip size="small" :color="colors[item.type]||'grey'" variant="tonal">{{ labels[item.type]||item.type }}</v-chip><span>{{ item.bars }} 根</span></div>
                <strong>{{ item.start }} → {{ item.end }}</strong>
                <p>{{ item.reason }}</p>
              </article>
            </div>
          </v-card-text>
        </v-card>
      </v-window-item>
    </v-window>
  </v-container>
</template>


<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { marketAPI } from '../api/market'
import * as echarts from 'echarts'

const route = useRoute(); const symbol = ref(String(route.query.symbol || 'BTCUSD')); const period = ref(String(route.query.period || 'M5')); const periods=['M1','M5','M15','H1','H4']; const symbols=ref([symbol.value]); const loading=ref(false); const error=ref(''); const segments=ref([]); const layerSegmentMap=ref({internal:[],swing:[],external:[]}); const bars=ref([]); const structureResult=ref(null); const tradePlans=ref([]); const opportunityDetails=ref({}); const opportunityLoading=ref({}); const chartRefs={internal:null,swing:null,external:null}; const charts={internal:null,swing:null,external:null}; const chartViewState={internal:{legendSelected:{}},swing:{legendSelected:{}},external:{legendSelected:{}}}; const activeLayer=ref('swing'); const layerKeys=['internal','swing','external']; let refreshTimer=null; let resetZoomOnNextRender=false
const labels={up:'上涨趋势',down:'下跌趋势',sideways:'箱体震荡',triangle:'收敛三角形',transition:'结构过渡'}; const colors={up:'success',down:'error',sideways:'info',triangle:'secondary',transition:'warning'}; const legendColors={up:'#3aa675',down:'#d95d55',sideways:'#4f91c4',triangle:'#8968b7',transition:'#d4a24c'}
const closeOf=x=>Number(x.close ?? x.close_price ?? 0); const timeOf=x=>{const utc=x?.timestamp_utc;const raw=(utc!==undefined&&utc!==null&&Number(utc)>0)?utc:(x?.timestamp??x?.time??0);const numeric=typeof raw==='number'?raw:(typeof raw==='string'&&/^\d+(\.\d+)?$/.test(raw)?Number(raw):NaN);if(Number.isFinite(numeric))return numeric>1e12?numeric:numeric*1000;const parsed=Date.parse(raw);return Number.isFinite(parsed)?parsed:0}; const stamp=x=>new Date(timeOf(x)).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})
const periodMs=p=>({M1:60000,M5:300000,M15:900000,H1:3600000,H4:14400000}[String(p).toUpperCase()]||300000)
function swings(rows){const out=[];const span=Math.max(2,Math.floor(rows.length/45));for(let i=span;i<rows.length-span;i++){const h=Number(rows[i].high??rows[i].high_price??closeOf(rows[i]));const l=Number(rows[i].low??rows[i].low_price??closeOf(rows[i]));const left=rows.slice(i-span,i).map(x=>Number(x.high??x.high_price??closeOf(x)));const right=rows.slice(i+1,i+span+1).map(x=>Number(x.high??x.high_price??closeOf(x)));const ll=rows.slice(i-span,i).map(x=>Number(x.low??x.low_price??closeOf(x)));const lr=rows.slice(i+1,i+span+1).map(x=>Number(x.low??x.low_price??closeOf(x)));if(h>=Math.max(...left,...right))out.push({index:i,type:'high',price:h});if(l<=Math.min(...ll,...lr))out.push({index:i,type:'low',price:l})}return out}
function classify(rows){if(rows.length<12)return 'transition';const sw=swings(rows), highs=sw.filter(x=>x.type==='high').slice(-5), lows=sw.filter(x=>x.type==='low').slice(-5);if(highs.length<3||lows.length<3)return'transition';const dh=highs.slice(-3).map((x,i,a)=>i?x.price-a[i-1].price:0).slice(1), dl=lows.slice(-3).map((x,i,a)=>i?x.price-a[i-1].price:0).slice(1);const scale=Math.max(1,Math.max(...rows.map(x=>Number(x.high??x.high_price??closeOf(x))))-Math.min(...rows.map(x=>Number(x.low??x.low_price??closeOf(x)))));const nh=(dh[0]+dh[1])/(2*scale),nl=(dl[0]+dl[1])/(2*scale);const rangeHigh=Math.max(...highs.map(x=>x.price)),rangeLow=Math.min(...lows.map(x=>x.price));const closes=rows.map(closeOf);const tail=closes.slice(-3);const upBreak=tail.every(v=>v>rangeHigh*1.0003),downBreak=tail.every(v=>v<rangeLow*0.9997);if(upBreak)return'up';if(downBreak)return'down';if(nh>.003&&nl>.003&&dh.every(v=>v>0)&&dl.every(v=>v>0))return'up';if(nh<-.003&&nl<-.003&&dh.every(v=>v<0)&&dl.every(v=>v<0))return'down';if(Math.abs(nh)<.004&&Math.abs(nl)<.004){const touches=sw.filter(x=>(x.type==='high'&&Math.abs(x.price-rangeHigh)/rangeHigh<.003)||(x.type==='low'&&Math.abs(x.price-rangeLow)/rangeLow<.003)).length;if(touches>=3)return'sideways'}return'transition'}
/* legacy build retained below for reference */
function build(rows){
  const step=Math.max(5,Math.floor(rows.length/60)); const window=Math.max(30,step*4); const raw=[];
  for(let i=0;i<rows.length;i+=step){const end=Math.min(rows.length,i+window);raw.push({i,end,type:classify(rows.slice(i,end))})}
  const confirmed=[];
  raw.forEach((item,index)=>{const last=confirmed.at(-1);if(!last){confirmed.push({type:item.type,startIndex:item.i,endIndex:item.end-1});return}if(item.type===last.type){last.endIndex=item.end-1;return}if(raw[index+1]?.type===item.type){confirmed.push({type:item.type,startIndex:item.i,endIndex:item.end-1})}else last.endIndex=item.end-1});
  confirmed.forEach((s,i)=>{s.endIndex=i+1<confirmed.length?confirmed[i+1].startIndex-1:rows.length-1});
  // 先识别趋势破坏：高点/低点后的深度回撤应切段，即使反向段尚未很长。
  const broken=[]; for(const s of confirmed){const part=rows.slice(s.startIndex,s.endIndex+1); if((s.type==='up'||s.type==='down')&&part.length>=Math.max(24,step*4)){const vals=part.map(closeOf); const extreme=s.type==='up'?Math.max(...vals):Math.min(...vals); const at=s.type==='up'?vals.lastIndexOf(extreme):vals.lastIndexOf(extreme); const tail=vals.slice(at+1); const span=Math.max(...vals)-Math.min(...vals); const retrace=span?Math.abs((tail.at(-1)||extreme)-extreme)/span:0; const recent=tail.slice(-Math.max(4,step)).filter((v,i,a)=>s.type==='up'?v<=a[Math.max(0,i-1)]:v>=a[Math.max(0,i-1)]).length; if(at>=8&&tail.length>=Math.max(8,step*2)&&retrace>=0.42&&recent>=Math.max(3,step-1)){const cut=s.startIndex+at+1; broken.push({...s,endIndex:cut-1}); broken.push({type:s.type==='up'?'down':'up',startIndex:cut,endIndex:s.endIndex}); continue}} broken.push(s)}
  // 普通反向短段仍需至少 4 个采样步长，避免单次噪声造成切换。
  // 校正窗口内的明显拐点：若下跌段先上涨创高，再持续回落，起点应落在高点确认处。
  const corrected=[]; for(const s of broken){const part=rows.slice(s.startIndex,s.endIndex+1); const vals=part.map(closeOf); const span=Math.max(...vals)-Math.min(...vals); if(vals.length>=20&&span>0&&(s.type==='down'||s.type==='up')){const extreme=s.type==='down'?Math.max(...vals):Math.min(...vals); const pivot=vals.lastIndexOf(extreme); const lead=s.type==='down'?vals[pivot]-vals[0]:vals[0]-vals[pivot]; if(pivot>=5&&pivot<=vals.length*.9&&lead/span>=.15&&vals.length-pivot>=6){corrected.push({type:s.type==='down'?'up':'down',startIndex:s.startIndex,endIndex:s.startIndex+pivot}); corrected.push({...s,startIndex:s.startIndex+pivot+1}); continue}} corrected.push(s)}
  const merged=[]; for(const s of corrected){const last=merged.at(-1);if(last&&last.type!==s.type&&s.endIndex-s.startIndex+1<step*3)last.endIndex=s.endIndex;else if(last&&last.type===s.type)last.endIndex=s.endIndex;else merged.push(s)}
  return merged.slice(-5).map((s,i,arr)=>{const part=rows.slice(s.startIndex,s.endIndex+1);return {...s,id:`s-${s.startIndex}`,bars:part.length,start:stamp(part[0]),end:stamp(part.at(-1)),support:Math.min(...part.map(x=>Number(x.low??x.low_price??closeOf(x)))),resistance:Math.max(...part.map(x=>Number(x.high??x.high_price??closeOf(x)))),confidence:s.type==='transition'?50:70,status:i===arr.length-1?'当前已确认':'已结束',reason:s.type==='up'?'高低点和收盘结构持续抬升（允许中途震荡/回撤）':s.type==='down'?'高低点和收盘结构持续下移（允许中途震荡/反弹）':s.type==='sideways'?'价格在区间内反复运行':'趋势证据发生冲突，等待确认'}})
}
const current=computed(()=>layerSegments(activeLayer.value).at(-1)); const eventDecisions=ref([]); const eventMatrix=ref([]); const observationPlans=ref([]); async function loadTradePlans(){try{const response=await marketAPI.getStructureTradePlans(symbol.value,period.value);tradePlans.value=Array.isArray(response?.plans)?response.plans:[];eventDecisions.value=Array.isArray(response?.event_decisions)?response.event_decisions:[];eventMatrix.value=Array.isArray(response?.event_matrix)?response.event_matrix:[];observationPlans.value=Array.isArray(response?.observation_plans)?response.observation_plans:[]}catch(e){tradePlans.value=[];eventDecisions.value=[];eventMatrix.value=[];observationPlans.value=[]}}
const layerState=layer=>(structureResult.value?.structure_hierarchy||{})[layer]||{}
const mapSegments=list=>{
  const rows=bars.value
  return (Array.isArray(list)?list:[]).slice(-5).map(s=>{
    const p=rows.slice(s.start_index,s.end_index+1)
    return {
      ...s,
      id:`${s.start_index}-${s.end_index}-${s.type}`,
      bars:p.length,
      start:p[0]?stamp(p[0]):'',
      end:p.at(-1)?stamp(p.at(-1)):'',
      reason:s.reason||'结构证据已计算',
    }
  })
}
const layerSegments=layer=>layerSegmentMap.value[layer]||[]
const layerPlans=layer=>{
  return tradePlans.value.filter(plan=>{
    const directionLayer=String(plan.direction_layer||'swing')
    const entryLayer=String(plan.entry_layer||directionLayer)
    return directionLayer===layer || entryLayer===layer
  })
}
const planStageLabel=plan=>{
  if(String(plan?.status||'')==='event_suppressed') return '暂停触发'
  return String(plan?.status||'')==='active'?'等待价格':'等待确认'
}
const layerLabel=value=>({internal:'INTERNAL',swing:'SWING',external:'EXTERNAL'}[String(value||'').toLowerCase()]||String(value||'--').toUpperCase())
const planEventLabel=value=>({bos:'BOS',choch:'CHOCH',liquidity_sweep:'扫单',retest:'回测',reclaim:'收复',hl_confirmed:'HL确认',lh_confirmed:'LH确认',hl_support_touched:'HL触碰',lh_press_touched:'LH触碰',breakout_confirmed:'突破'}[String(value||'').toLowerCase()]||String(value||'事件'))
const planTypeLabel=value=>({trend_continuation:'趋势延续',range_breakout:'箱体突破',internal_momentum:'内部动量',structure_reversal:'结构反转',early_reversal:'早期反转',liquidity_reversal:'扫单反转',swing_liquidity_reversal:'Swing扫单反转',internal_liquidity_reversal:'Internal扫单反转',swing_pullback:'Swing回撤',internal_pullback:'Internal回撤',event_confirmation:'事件确认',range_reclaim:'箱体收复'}[String(value||'').toLowerCase()]||String(value||'结构计划'))
const planLabel=plan=>{
  const setup=planTypeLabel(plan?.plan_type||plan?.setup_type)
  const layer=plan?.event_layer||plan?.entry_layer
  const event=plan?.event_type
  return layer && event ? `${setup} · ${layerLabel(layer)} ${planEventLabel(event)}` : setup
}
const planConsumptionLabel=plan=>{
  const summary=plan?.subscription_summary||{}
  const expected=Number(summary.expected_count ?? 0)
  const consumed=Number(summary.consumed_count || 0)
  const stage=planStageLabel(plan)
  return `${stage} · 已消费 ${consumed} 次 / 应消费 ${expected} 次`
}
const formatPlanTime=value=>value?new Date(Number(value)*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'--'
const formatPlanPrice=value=>{const number=Number(value);if(!Number.isFinite(number)||number<=0)return '--';const decimals=number<10?5:number<1000?3:2;return number.toLocaleString('zh-CN',{minimumFractionDigits:decimals,maximumFractionDigits:decimals})}
const opportunityStatusLabel=value=>({initial_pending:'首仓待执行',initial_ordered:'首仓已下单',initial_filled:'首仓已成交',initial_partially_filled:'首仓部分成交',initial_failed:'首仓失败',protection_pending:'等待保护止损',breakout_eligible:'突破阶段可执行',breakout_ordered:'突破已下单',breakout_filled:'突破已成交',breakout_partially_filled:'突破部分成交',breakout_failed:'突破失败'}[value]||value||'尚未执行')
async function loadOpportunity(plan){const id=String(plan?.opportunity_id||'');if(!id)return;if(opportunityDetails.value[id]){const next={...opportunityDetails.value};delete next[id];opportunityDetails.value=next;return}opportunityLoading.value={...opportunityLoading.value,[id]:true};try{const result=await marketAPI.getStructureOpportunity(symbol.value,id,period.value);if(result?.found){opportunityDetails.value={...opportunityDetails.value,[id]:result}}}catch(e){error.value='机会详情加载失败'}finally{opportunityLoading.value={...opportunityLoading.value,[id]:false}}
}
const modeLabel=value=>value==='live'?'实盘':'模拟盘'
const executionLabel=value=>({unconsumed:'待消费',claimed:'已领取',triggered:'已触发',ordered:'已下单',filled:'已成交',rejected:'已拒绝',expired:'已过期',canceled:'已取消',released:'已释放'}[value]||value||'待消费')
const executionColor=value=>({unconsumed:'warning',claimed:'info',triggered:'info',ordered:'primary',filled:'success',rejected:'error',expired:'grey',canceled:'grey',released:'secondary'}[value]||'grey')
const gateLabel=value=>({eligible:'允许执行',snapshot_missing:'共享快照缺失',no_direction:'未形成方向',no_new_trigger:'没有新触发',decision_cooldown:'决策冷却中',entry_guard:'入场门禁拦截',position_policy:'持仓规则拦截',invalid_volume:'手数无效',position_limit:'持仓上限',risk_limit:'风险上限',claim_conflict:'计划已消费',technical_failure:'技术失败',missing_audit:'尚未评估'}[value]||value||'尚未评估')
const gateColor=value=>value==='eligible'?'success':(['snapshot_missing','claim_conflict','technical_failure'].includes(value)?'error':(value==='missing_audit'?'grey':'warning'))
const hierarchyLabels={internal:'Internal 内部结构',swing:'Swing 主结构',external:'External 外部结构'}
const primaryStructureLabel=value=>({trend_up:'上涨趋势',trend_down:'下跌趋势',range:'箱体背景',transition:'结构过渡'}[value]||'结构过渡')
const primaryColor=value=>value==='trend_up'?'success':value==='trend_down'?'error':value==='range'?'info':'warning'
const patternLabel=(value,bias,detail)=>{
  const pattern=String(value||'')
  if(pattern==='trend'){
    const channel=String(detail?.channel_bias||'')
    const dir=channel||bias
    if(dir==='up') return '上涨趋势'
    if(dir==='down') return '下跌趋势'
    return '趋势'
  }
  if(pattern==='descending_channel') return '下降趋势'
  if(pattern==='ascending_channel') return '上升趋势'
  return {range:'箱体震荡',triangle:'三角形',converging_triangle:'收敛三角形',diverging_triangle:'扩散三角形',ascending_triangle:'上升三角形',descending_triangle:'下降三角形',trendline:'趋势线',none:'未形成'}[pattern]||pattern||'未识别'
}
const patternColor=(value,bias)=>{
  const pattern=String(value||'')
  if(pattern==='range'||pattern.includes('triangle')) return 'info'
  if(pattern==='trend') return bias==='down'?'error':bias==='up'?'success':'secondary'
  return 'grey'
}
const patternPhaseLabel=(pattern,value)=>{
  const phase=String(value||'')
  if(String(pattern||'').includes('triangle') || pattern==='range'){
    return {forming:'形成中',mature:'震荡成熟',breakout_confirmed:'突破已确认',continuation:'震荡延续'}[phase]||phase||'--'
  }
  if(pattern==='trend'){
    return {forming:'形成中',continuation:'趋势推进',pullback:'回撤整理',mature:'趋势成熟',reversal_candidate:'反转候选',reversal_confirmed:'反转确认'}[phase]||phase||'--'
  }
  return {forming:'形成中',continuation:'延续',pullback:'回撤中',mature:'成熟',breakout_confirmed:'突破已确认'}[phase]||phase||'--'
}
const eventLabel=value=>{const event=typeof value==='string'?value:(value?.type||value?.event_type||'');return {bos:'BOS 延续突破',choch:'CHoCH 结构转向',retest:'回测确认',reclaim:'重新收复',hl_confirmed:'HL 已确认',lh_confirmed:'LH 已确认',hl_support_touched:'HL 支撑已触碰',lh_press_touched:'LH 压力已触碰',liquidity_sweep:'扫单回收',breakout_confirmed:'突破已确认',none:'无新事件'}[event]||event||'无新事件'}
const eventColor=value=>{const event=typeof value==='string'?value:(value?.type||value?.event_type||'');return event==='choch'||event==='false_breakout'?'warning':event==='bos'||event==='breakout_confirmed'?'success':event==='liquidity_sweep'?'secondary':'grey'}
const patternDetail=value=>{
  if(typeof value==='string') return value
  if(!value||typeof value!=='object') return ''
  const labels={pattern:'形态',status:'状态',segment_bars:'结构段K线',high_touches:'上沿触碰',low_touches:'下沿触碰',inside_ratio:'内部收盘',width_atr:'宽度ATR'}
  return ['pattern','status','segment_bars','high_touches','low_touches','inside_ratio','width_atr']
    .filter(key=>value[key]!==null&&value[key]!==undefined&&value[key]!=='')
    .slice(0,4)
    .map(key=>`${labels[key]} ${typeof value[key]==='number'?Number(value[key]).toFixed(2):value[key]}`)
    .join(' · ')
}
const levelPrice=value=>{
  if(value==null) return 0
  if(typeof value==='number') return Number.isFinite(value)?value:0
  const price=Number(value.price)
  return Number.isFinite(price)&&price>0?price:0
}
const setupMappingLabel=item=>{const pattern=item?.pattern;const event=typeof item?.event==='string'?item.event:item?.event?.type;if(event==='bos'||event==='breakout_confirmed')return '突破类 SETUP 可评估';if(event==='choch'||event==='false_breakout')return '反转/假突破类 SETUP 可评估';if(pattern==='trend')return '趋势回撤类 SETUP 可评估';return '等待事件满足执行条件'}
// 结构时间轴条已停用，保留空样式函数避免旧模板调用导致渲染中断。
const stripStyle=()=>({display:'none'})
const stateLabel=value=>({up:'上涨趋势',down:'下跌趋势',bullish:'上涨趋势',bearish:'下跌趋势',range:'箱体/三角形',undetermined:'尚未确认'}[value]||'结构过渡')
const trendPhaseLabel=value=>({strong:'强势',mature:'成熟',weakening:'衰竭预警',failed:'趋势失败',undetermined:'尚未确认'}[value]||'未评估')
const barStamp=index=>bars.value[index]?stamp(bars.value[index]):''
const eventFilter=ref('structure')
const eventScope=computed(()=>activeLayer.value)
const hierarchyScopeLabel=value=>({internal:'Internal',small:'Internal',swing:'Swing',medium:'Swing',major:'Swing',external:'External',large:'External'}[value]||'')
const normalizeEventScope=value=>{
  const scope=String(value||'').toLowerCase()
  if(['internal','small'].includes(scope)) return 'internal'
  if(['external','large'].includes(scope)) return 'external'
  return 'swing'
}
const formatEventLevel=value=>{const number=Number(value);if(!Number.isFinite(number)||number<=0)return '--';const decimals=number<10?5:number<1000?3:2;return number.toLocaleString('zh-CN',{minimumFractionDigits:decimals,maximumFractionDigits:decimals})}
const groupedEventsFor=layer=>{
  const source=structureResult.value||{}
  const byScope={
    internal: Array.isArray(source.internal_events)?source.internal_events:[],
    swing: Array.isArray(source.major_events)?source.major_events:[],
    external: Array.isArray(source.external_events)?source.external_events:[],
  }
  const rows=[...byScope[layer]||[]].reverse()
  const wanted=eventFilter.value
  const filtered=rows.filter(event=>{
    const type=String(event?.type||'')
    if(wanted==='structure') return type==='bos'||type==='choch'
    if(wanted==='sweep') return type==='liquidity_sweep'
    return Boolean(type)
  })
  const grouped=[]
  for(const event of filtered){
    const type=String(event.type||'')
    const direction=String(event.direction||'')
    const level=Number(event.level||0)
    const scope=String(event.scope||'')
    const rounded=Number.isFinite(level)?level.toFixed(2):''
    const last=grouped.at(-1)
    const sameSweep=type==='liquidity_sweep' && last && last.type==='liquidity_sweep' && last.direction===direction && last.rounded===rounded && last.scope===scope
    if(sameSweep){
      last.count += 1
      last.latestIndex = event.index
      continue
    }
    grouped.push({
      key:`${type}-${direction}-${event.index}-${grouped.length}`,
      type, direction, level, scope: scope || eventScope.value, rounded,
      index: event.index, latestIndex: event.index, count: 1,
    })
    if(grouped.length>=8) break
  }
  return grouped
}
const groupedStructureEvents=computed(()=>groupedEventsFor(activeLayer.value))
const layerEvents=layer=>groupedEventsFor(layer)
function setChartRef(layer, el){ chartRefs[layer]=el }
function captureChartView(layer){
  const chart=charts[layer]
  if(!chart) return
  let option={}
  try{ option=chart.getOption()||{} }catch{ return }
  const legend=Array.isArray(option.legend)?option.legend[0]:option.legend
  const zooms=option.dataZoom||[]
  const state=chartViewState[layer]
  if(legend&&legend.selected) state.legendSelected={...legend.selected}
  if(zooms.length){
    const z=zooms[0]||{}
    state.dataZoom={start:z.start,end:z.end,startValue:z.startValue,endValue:z.endValue}
  }
}
function bindChartViewEvents(layer, chart){
  if(chart.__viewBound) return
  chart.__viewBound=true
  chart.on('legendselectchanged', params=>{
    chartViewState[layer].legendSelected={...(params.selected||{})}
  })
  chart.on('datazoom', ()=>captureChartView(layer))
}
function renderChartUnsafe(){
  renderLayerChart(activeLayer.value)
}
function renderLayerChart(layer){
  const el=chartRefs[layer]
  if(!el||!bars.value.length)return
  captureChartView(layer)
  if(charts[layer] && charts[layer].getDom()!==el){
    try{ charts[layer].dispose() }catch{}
    charts[layer]=null
  }
  if(!charts[layer]) charts[layer]=echarts.init(el)
  bindChartViewEvents(layer, charts[layer])
  const chart=charts[layer]
  const rows=bars.value; const result=structureResult.value||{}
  const data=rows.map(x=>[Number(x.open??x.open_price??closeOf(x)),Number(x.close??x.close_price??0),Number(x.low??x.low_price??closeOf(x)),Number(x.high??x.high_price??closeOf(x))])
  const palette=['rgba(73,145,196,.12)','rgba(75,170,123,.12)','rgba(224,163,73,.14)','rgba(207,91,91,.12)','rgba(133,105,190,.12)']
  const area=layerSegments(layer).filter(s=>Number.isInteger(s.start_index)&&Number.isInteger(s.end_index)&&s.end_index>=s.start_index).map((s,i)=>[{xAxis:s.start_index,itemStyle:{color:palette[i%palette.length]}},{xAxis:s.end_index,itemStyle:{color:palette[i%palette.length]}}])
  const pivotKey={internal:'small',swing:'medium',external:'large'}[layer]; const pivots=Array.isArray(result.pivot_levels?.[pivotKey])?result.pivot_levels[pivotKey]:[]
  const pivotMarks=pivots.filter(p=>Number.isInteger(p.index)&&p.index>=0&&p.index<rows.length).slice(-24).map(p=>({coord:[p.index,Number(p.price)],value:p.label|| (p.kind==='high'?'H':'L'),name:p.label||p.kind,itemStyle:{color:p.kind==='high'?'#c84f43':'#287a60'}}))
  const sourceEvents={internal:result.internal_events,swing:result.major_events,external:result.external_events}[layer]||[]; const eventMarks=(Array.isArray(sourceEvents)?sourceEvents:[]).filter(e=>Number.isInteger(e.index)&&e.index>=0&&e.index<rows.length).map(e=>({value:[e.index,Number(e.level||closeOf(rows[e.index]))],event:e,name:e.type}))
  const eventDefs=[['bos','up','向上 BOS','circle','#16845f'],['bos','down','向下 BOS','circle','#c84f43'],['choch','up','向上 CHoCH','diamond','#16845f'],['choch','down','向下 CHoCH','diamond','#c84f43'],['liquidity_sweep','up','向上扫单','triangle','#16845f'],['liquidity_sweep','down','向下扫单','triangle','#c84f43']]
  const eventSeries=eventDefs.map(([type,direction,name,symbol,color])=>({name,type:'scatter',data:eventMarks.filter(p=>p.event.type===type&&p.event.direction===direction),symbol,symbolRotate:type==='liquidity_sweep'?(direction==='up'?0:180):0,symbolSize:type==='liquidity_sweep'?12:18,itemStyle:{color},label:{show:type!=='liquidity_sweep',formatter:name,color,fontSize:9},z:8}))
  const trend=(Array.isArray(result.trendlines)?result.trendlines:[]).map((line,n)=>({type:'line',name:`${line.kind==='support'?'上涨支撑':'下降压力'} · ${line.level||''}`,data:rows.map((_,i)=>i<line.start_index||i>line.end_index?'-':Number(line.start_price)+(Number(line.slope)||0)*(i-Number(line.start_index))),symbol:'none',lineStyle:{color:line.kind==='support'?'#2b9b72':'#d95d55',type:'dashed',width:2},showSymbol:false,connectNulls:false,z:3}))
  const candidate=result.active_candidate;const candidateSeries=candidate&&Number.isInteger(candidate.swing_index)?[{type:'line',name:'候选结构（未确认）',data:rows.map((row,i)=>i<candidate.swing_index?'-':(i===candidate.swing_index?Number(candidate.level):closeOf(row))),symbol:'none',lineStyle:{color:'#ed9b32',type:'dashed',width:3,opacity:.9},showSymbol:false,connectNulls:true,z:9}]:[]
  const range=result.range?.active?[{name:'箱体上沿',yAxis:Number(result.range.top)},{name:'箱体下沿',yAxis:Number(result.range.bottom)}]:[]
  const eventLines=(Array.isArray(result.events)?result.events:[]).filter(e=>Number.isInteger(e.index)).map(e=>({name:e.type==='choch'?'CHoCH':e.type==='bos'?'BOS':'流动性扫过',xAxis:e.index,lineStyle:{color:e.direction==='up'?'#16845f':'#c84f43',type:'dotted',width:1},label:{show:false}}))
  const legendData=['K线',...eventSeries.map(item=>item.name),...trend.map(item=>item.name),...(candidateSeries.length?['候选结构（未确认）']:[])]
  const xData=rows.map(stamp)
  const view=chartViewState[layer]||{}
  let dataZoom=[{type:'inside'},{type:'slider',height:18,bottom:8}]
  const savedZoom=(!resetZoomOnNextRender && view.dataZoom) ? view.dataZoom : null
  if(savedZoom){
    const useValues=xData.includes(savedZoom.startValue)&&xData.includes(savedZoom.endValue)
    dataZoom=dataZoom.map(z=>useValues
      ? {...z,startValue:savedZoom.startValue,endValue:savedZoom.endValue}
      : (savedZoom.start!=null&&savedZoom.end!=null?{...z,start:savedZoom.start,end:savedZoom.end}:z))
  }
  resetZoomOnNextRender=false
  chart.setOption({animation:false,tooltip:{trigger:'axis',axisPointer:{type:'cross'}},legend:{top:0,type:'scroll',data:legendData,selected:view.legendSelected||{}},grid:{left:55,right:35,top:38,bottom:58},xAxis:{type:'category',data:xData,axisLabel:{hideOverlap:true}},yAxis:{scale:true},dataZoom,series:[{name:'K线',type:'candlestick',data,itemStyle:{color:'#1f9d72',color0:'#d95d55',borderColor:'#1f9d72',borderColor0:'#d95d55'},markPoint:{symbol:'circle',symbolSize:9,data:pivotMarks,label:{show:true,position:'top',fontSize:10,formatter:p=>p.value}},markLine:{silent:true,symbol:'none',data:[...range,...eventLines]}},...trend,...candidateSeries,...eventSeries]},true)
}

function resizeChart(){Object.values(charts).forEach(item=>item?.resize())}
function safeRenderChart(){try{renderChartUnsafe()}catch(err){console.error('[StructureAnalysis] chart overlay error',err)}}
function renderChart(){safeRenderChart()}
async function load(){loading.value=true;error.value='';try{const res=await marketAPI.getKlines(symbol.value,period.value,600);const raw=Array.isArray(res?.data)?res.data:(Array.isArray(res?.klines)?res.klines:(Array.isArray(res?.results)?res.results:(Array.isArray(res?.data?.klines)?res.data.klines:(Array.isArray(res?.data?.data)?res.data.data:[]))));const now=Date.now()+periodMs(period.value);const filtered=raw.filter(x=>{const t=timeOf(x);return t>0&&t<=now});const rows=(filtered.length?filtered:raw).slice().sort((a,b)=>timeOf(a)-timeOf(b));bars.value=rows;if(!rows.length){error.value=`暂无可用K线（${symbol.value} · ${period.value}）`;return}let backend=null;try{const sr=await marketAPI.getMarketStructure(symbol.value,period.value,600);backend=sr?.data;structureResult.value=backend||null}catch(structureError){structureResult.value=null;error.value='结构分析暂时不可用，已显示原始K线'}layerSegmentMap.value={internal:mapSegments(backend?.layer_segments?.internal||backend?.structure_hierarchy?.internal?.segments||[]),swing:mapSegments(backend?.layer_segments?.swing||backend?.segments||[]),external:mapSegments(backend?.layer_segments?.external||backend?.structure_hierarchy?.external?.segments||[])}; segments.value=layerSegmentMap.value.swing; if(!segments.value.length) segments.value=build(rows); await nextTick(); renderChart()}catch(e){error.value=e?.response?.data?.detail||'K线数据加载失败'}finally{loading.value=false}}
async function loadSymbols(){try{const res=await marketAPI.getSymbols();const values=Array.from(new Set((res?.symbols||res?.data||[]).map(item=>typeof item==='string'?item:(item.symbol||item.value||'')).filter(Boolean)));symbols.value=values;if(!values.includes(symbol.value))symbol.value=values[0]||''}catch(e){/* 保留当前品种，行情接口失败不阻断页面 */}}
watch(period,()=>{resetZoomOnNextRender=true;if(symbol.value){load();loadTradePlans()}});watch(symbol,()=>{resetZoomOnNextRender=true;if(symbol.value){load();loadTradePlans()}});watch(activeLayer,()=>nextTick().then(renderChart));onMounted(async()=>{window.addEventListener('resize',resizeChart);await loadSymbols();if(symbol.value){await load();await loadTradePlans()}refreshTimer=setInterval(()=>{if(symbol.value){load();loadTradePlans()}},30000)});onUnmounted(()=>{if(refreshTimer)clearInterval(refreshTimer);window.removeEventListener('resize',resizeChart);Object.values(charts).forEach(item=>item?.dispose())})
</script>
<style scoped>
.structure-strip-title,.structure-strip{display:none !important}
</style>
<style scoped>
.structure-strip-title{margin-top:10px;font-size:.78rem;color:#71837b}.structure-strip{display:flex;width:100%;height:28px;border-radius:6px;overflow:hidden;background:#eef2f0;border:1px solid #dbe5e0}.structure-strip-segment{display:flex;align-items:center;justify-content:center;min-width:4px;color:#fff;font-size:.7rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;border-right:2px solid rgba(255,255,255,.8)}
</style>

<style scoped>.structure-page{max-width:1500px;padding:28px}.hero{display:flex;justify-content:space-between;gap:24px;align-items:center;padding:28px 30px;margin-bottom:20px;border-radius:22px;color:#f5fffa;background:linear-gradient(125deg,#173d35,#277d61)}.hero span{font-size:.72rem;letter-spacing:.16em;color:#f4cf77;font-weight:800}.hero h1{margin:5px 0;font-size:2rem}.hero p{margin:0;color:#cce4da}.controls{display:flex;gap:10px;align-items:center;min-width:390px}.summary{height:100%;border:1px solid #dbe8e1}.summary h2{margin:6px 0 10px;color:#204f42}.summary p{color:#60736b;min-height:34px}.stats{display:flex;gap:18px;color:#60736b;font-size:.85rem}.timeline{display:flex;gap:18px;overflow:auto;padding:8px 0}.segment{display:flex;gap:8px;min-width:150px}.segment i{width:8px;border-radius:8px;display:block}.segment small,.segment p{display:block;color:#71837b;font-size:.78rem;margin:4px 0}.segment.active strong{color:#167052}.segment-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}.segment-grid article{padding:14px;border:1px solid #dbe8e1;border-radius:14px;background:#fbfdfb}.segment-grid article.active{border-color:#2d9871;box-shadow:0 5px 18px #2d987122}.card-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}.segment-grid p{height:38px;color:#60736b;font-size:.82rem}.segment-grid small{color:#71837b}.empty{text-align:center;color:#71837b}@media(max-width:850px){.hero{align-items:stretch;flex-direction:column}.controls{min-width:0;width:100%}.segment-grid{grid-template-columns:1fr 1fr}}@media(max-width:600px){.structure-page{padding:16px}.controls{flex-wrap:wrap}.controls>*{flex:1}.segment-grid{grid-template-columns:1fr}}</style>
<style scoped>
.hierarchy-grid,.pattern-list{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.hierarchy-item,.pattern-list article{padding:14px;border:1px solid #dbe8e1;border-radius:10px;background:#fbfdfb}
.state-row{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:5px 0;border-bottom:1px dashed #e3ece7;color:#71837b;font-size:.8rem}.state-row strong{color:#315f50;text-align:right}
.hierarchy-item p,.pattern-list p{margin:6px 0;color:#60736b;font-size:.82rem}
.hierarchy-item small{display:block;color:#71837b;margin-top:3px}
.pattern-list{grid-template-columns:repeat(2,1fr)}
@media(max-width:850px){.hierarchy-grid,.pattern-list{grid-template-columns:1fr}}
</style>
<style scoped>
.plan-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.plan-grid>article{padding:16px;border:1px solid #dbe8e1;border-radius:14px;background:#fbfdfb;min-width:0}
.plan-grid p{margin:10px 0;color:#526860;font-size:.88rem}
.plan-grid>article>small{color:#71837b}
.plan-values{display:flex;gap:16px;flex-wrap:wrap;color:#304e44;font-size:.86rem;font-weight:600}
.plan-source-summary{display:flex;gap:10px;flex-wrap:wrap;margin-top:7px;color:#6b8178;font-size:.72rem}
.plan-source-summary span{padding:3px 7px;border:1px solid #dce9e3;border-radius:6px;background:#f7fbf9}
.plan-levels{display:grid;gap:7px;margin-top:10px;padding:9px 10px;border-radius:10px;background:#f1f6f3}.plan-levels>div{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.plan-levels small{min-width:58px;color:#657970}
.subscription-summary{display:flex;gap:7px;flex-wrap:wrap;margin-top:14px;padding-top:12px;border-top:1px dashed #d7e3dd}
.subscription-panel{margin-top:10px}
.subscription-row{display:flex;justify-content:space-between;gap:14px;padding:10px 0;border-bottom:1px solid #edf2ef}
.subscription-row:last-child{border-bottom:0}
.subscription-row small,.subscription-status small{display:block;color:#71837b;font-size:.75rem;margin-top:3px}
.subscription-status{text-align:right;max-width:55%}
.no-subscriber{display:block;margin-top:12px;color:#8a9a93}
@media(max-width:900px){.plan-grid{grid-template-columns:1fr}}
</style>
<style scoped>
.review-card{border:1px solid #dce8e2;background:linear-gradient(145deg,#fff,#f7fbf8)}
.review-head{display:flex;align-items:center;gap:12px;flex-wrap:wrap;color:#60736b}.review-head strong{color:#29483f;font-size:1rem}
.review-summary{margin:14px 0;color:#29483f;font-size:1rem}.review-metrics{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}.review-metrics span{padding:6px 10px;border-radius:8px;background:#edf5f1;color:#42665a;font-size:.8rem}
.review-card h3{margin:15px 0 8px;color:#315f50;font-size:.95rem}.review-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.review-list article{padding:12px;border:1px solid #e0ebe5;border-radius:10px;background:#fff}.review-list article strong{margin-left:6px;color:#315f50}.review-list p{margin:7px 0;color:#526860}.review-list small{color:#71837b}.review-history{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-top:16px;padding-top:12px;border-top:1px dashed #d7e3dd;color:#71837b;font-size:.8rem}
@media(max-width:850px){.review-list{grid-template-columns:1fr}}
</style>

<style scoped>
.event-filters{display:flex;gap:6px;flex-wrap:wrap}
.event-stack{display:flex;flex-direction:column;gap:8px}
.stacked-panels{display:flex;flex-direction:column;gap:12px}
.compact-events{gap:4px;max-height:260px;overflow:auto}
.tall-events{max-height:420px}

.event-item{padding:10px 12px;border:1px solid #dbe8e1;border-radius:10px;background:#fbfdfb}
.compact-events .event-item{padding:6px 8px;border-radius:8px}
.event-item.event-up{border-left:4px solid #2d9871}
.event-item.event-down{border-left:4px solid #d45b52}
.event-main{display:flex;align-items:center;gap:8px;flex-wrap:wrap;color:#29483f}
.compact-events .event-main{gap:6px;font-size:.78rem}
.event-time,.event-count{color:#71837b}
.compact-plans{grid-template-columns:1fr;gap:8px}
.compact-plans article{padding:10px 12px}
.compact-plans p{margin:6px 0 0;font-size:.78rem}.plan-chain,.plan-confirm{margin-top:4px;color:#6b8178;font-size:.7rem}
.h-100{height:100%}
.event-meta{display:flex;gap:10px;flex-wrap:wrap;margin-top:4px;color:#71837b;font-size:.78rem}
.decision-card{border:1px solid #dbe8e1;background:#fbfdfb}.decision-list{display:flex;flex-direction:column;gap:8px;max-height:260px;overflow:auto}.decision-item{padding:9px 10px;border:1px solid #e0eae4;border-radius:9px;background:#fff}.decision-item>div{display:flex;align-items:center;gap:8px;flex-wrap:wrap;color:#31564b;font-size:.78rem}.decision-item p{margin:5px 0;color:#60736b;font-size:.72rem;line-height:1.4}.decision-item small{color:#84918b;font-size:.66rem}.matrix-summary{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #edf2ef;color:#718078;font-size:.76rem}.matrix-summary strong{color:#31564b}.matrix-help{margin:10px 0 0;color:#74827c;font-size:.7rem;line-height:1.5}.observation-summary{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;padding-top:9px;border-top:1px dashed #d7e3dd;color:#718078;font-size:.68rem}.observation-summary span{padding:4px 7px;border-radius:6px;background:#f1f6f3}
</style>
