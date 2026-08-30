<template>
  <v-container fluid class="structure-page">
    <section class="hero">
      <div><span>MARKET STRUCTURE</span><h1>{{ symbol }} · {{ period }} 结构分析</h1><p>连续滑动识别行情结构，保留最近 5 个已确认结构段。</p></div>
      <div class="controls"><v-select v-model="symbol" :items="symbols" label="品种" density="compact" hide-details variant="outlined"/><v-select v-model="period" :items="periods" label="周期" density="compact" hide-details variant="outlined"/><v-btn color="primary" :loading="loading" @click="load">刷新</v-btn></div>
    </section>
    <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
    <v-alert v-if="structureResult" type="info" variant="tonal" density="compact" class="mb-4"><strong>结构层级说明：</strong>背景主结构为 {{ stateLabel(structureResult.major_state || structureResult.current_state) }}；当前局部形态为 {{ localStateLabel(structureResult) }}。两者不一致时，表示大背景中的局部整理或回撤。</v-alert>
    <v-card class="mb-4 plan-card">
      <v-card-title>结构交易计划</v-card-title>
      <v-card-subtitle>行情层统一生成；每个匹配部署独立订阅，并按“计划 + 部署”最多消费一次。</v-card-subtitle>
      <v-card-text>
        <div v-if="tradePlans.length" class="plan-grid">
          <article v-for="plan in tradePlans" :key="plan.plan_id">
            <div class="card-head"><v-chip size="small" :color="plan.direction==='buy'?'success':plan.direction==='sell'?'error':'info'" variant="tonal">{{ plan.direction==='buy'?'买入':plan.direction==='sell'?'卖出':'观察' }}</v-chip><strong>{{ plan.setup_type }}</strong><span>{{ plan.status==='active'?'等待价格':'等待确认' }}</span></div>
            <div class="plan-values"><span>入场 {{ Number(plan.entry_price||0).toFixed(2) }}</span><span>止损 {{ Number(plan.stop_loss||0).toFixed(2) }}</span><span>止盈 {{ Number(plan.take_profit||0).toFixed(2) }}</span></div>
            <p>{{ plan.reason || '结构条件尚未满足' }}</p>
            <small>产生于 {{ formatPlanTime(plan.generated_at) }} · 有效至 {{ formatPlanTime(plan.expires_at) }}</small>
            <div class="subscription-summary">
              <v-chip size="x-small" variant="tonal">订阅策略 {{ plan.subscription_summary?.strategy_count || 0 }}</v-chip>
              <v-chip size="x-small" color="primary" variant="tonal">运行部署 {{ plan.subscription_summary?.deployment_count || 0 }}</v-chip>
              <v-chip size="x-small" color="success" variant="tonal">已消费 {{ plan.subscription_summary?.consumed_count || 0 }}</v-chip>
              <v-chip size="x-small" color="warning" variant="tonal">待消费 {{ plan.subscription_summary?.unconsumed_count || 0 }}</v-chip>
            </div>
            <v-expansion-panels v-if="plan.subscriptions?.length" variant="accordion" class="subscription-panel">
              <v-expansion-panel>
                <v-expansion-panel-title>查看 {{ plan.subscriptions.length }} 个部署的消费明细</v-expansion-panel-title>
                <v-expansion-panel-text>
                  <div v-for="item in plan.subscriptions" :key="item.deployment_id" class="subscription-row">
                    <div><strong>{{ item.strategy_name }}</strong><small>{{ item.account_name }} · {{ modeLabel(item.execution_mode) }}</small></div>
                    <div class="subscription-status"><v-chip size="x-small" :color="executionColor(item.execution_status)" variant="tonal">{{ executionLabel(item.execution_status) }}</v-chip><small v-if="item.order_id">订单 {{ item.order_id }}</small><small v-if="item.execution_reason">{{ item.execution_reason }}</small></div>
                  </div>
                </v-expansion-panel-text>
              </v-expansion-panel>
            </v-expansion-panels>
            <small v-else class="no-subscriber">暂无正在运行的匹配部署；计划仍保留在行情层。</small>
          </article>
        </div>
        <div v-else class="empty">当前没有结构交易计划</div>
      </v-card-text>
    </v-card>
    <v-card class="mb-4 review-card">
      <v-card-title class="d-flex align-center ga-2"><v-icon color="primary">mdi-calendar-search</v-icon>结构信号每日复盘</v-card-title>
      <v-card-subtitle>每天北京时间 06:00 集中复盘前24小时结构计划；过去12小时无行情时自动跳过并记录原因。</v-card-subtitle>
      <v-card-text>
        <div v-if="latestStructureReview">
          <div class="review-head"><strong>{{ latestStructureReview.review_date }}</strong><v-chip size="small" :color="reviewStatusColor(latestStructureReview.status)" variant="tonal">{{ reviewStatusLabel(latestStructureReview.status) }}</v-chip><span>最后行情 {{ formatPlanTime(latestStructureReview.latest_market_at) }}</span></div>
          <v-alert v-if="latestStructureReview.status==='skipped'" type="warning" variant="tonal" density="compact" class="mt-3">{{ latestStructureReview.skip_reason }}</v-alert>
          <v-alert v-else-if="latestStructureReview.status==='failed'" type="error" variant="tonal" density="compact" class="mt-3">{{ latestStructureReview.error }}</v-alert>
          <template v-else-if="latestStructureReview.review">
            <p class="review-summary">{{ latestStructureReview.review.summary || '复盘已完成' }}</p>
            <div class="review-metrics"><span>计划 {{ latestStructureReview.evidence?.metrics?.plan_count || 0 }}</span><span>可交易 {{ latestStructureReview.evidence?.metrics?.tradable_plan_count || 0 }}</span><span>触发 {{ latestStructureReview.evidence?.metrics?.triggered_count || 0 }}</span><span>止盈 {{ latestStructureReview.evidence?.metrics?.target_hit_count || 0 }}</span><span>止损 {{ latestStructureReview.evidence?.metrics?.stop_hit_count || 0 }}</span></div>
            <h3>问题分析</h3><div v-if="latestStructureReview.review.problems?.length" class="review-list"><article v-for="(item,index) in latestStructureReview.review.problems" :key="`problem-${index}`"><v-chip size="x-small" :color="severityColor(item.severity)" variant="tonal">{{ item.severity || 'medium' }}</v-chip><strong>{{ item.category }}</strong><p>{{ item.analysis }}</p><small>证据：{{ item.evidence }}</small></article></div><div v-else class="empty">本次未发现明确问题</div>
            <h3>改进计划</h3><div v-if="latestStructureReview.review.improvement_plan?.length" class="review-list"><article v-for="(item,index) in latestStructureReview.review.improvement_plan" :key="`improvement-${index}`"><strong>{{ item.parameter }}</strong><p>{{ item.current_value }} → {{ item.suggested_value }}</p><small>{{ item.reason }}；验证：{{ item.validation }}</small></article></div><div v-else class="empty">本次没有可靠的调参建议</div>
          </template>
          <div v-if="structureReviews.length>1" class="review-history"><span>最近记录</span><v-chip v-for="item in structureReviews.slice(0,7)" :key="item.review_id" size="x-small" :color="reviewStatusColor(item.status)" variant="tonal" @click="selectedReviewId=item.review_id">{{ item.review_date }}</v-chip></div>
        </div>
        <div v-else class="empty">尚未生成每日结构信号复盘，首次任务将在北京时间 06:00 执行。</div>
      </v-card-text>
    </v-card>
    <v-card v-if="bars.length" class="chart-card mb-4"><v-card-title>K线与结构段</v-card-title><v-card-text><div ref="chartRef" class="chart" style="height:480px;width:100%"></div><div class="structure-strip-title">结构时间轴（按 K 线数量）</div><div class="structure-strip"><div v-for="(item,index) in segments" :key="`strip-${item.id}`" class="structure-strip-segment" :style="stripStyle(item,index)" :title="`${labels[item.type]||item.type} · ${item.start} → ${item.end} · 强度 ${item.strength ?? item.confidence ?? 0}%`"><span>{{ labels[item.type]||item.type }}</span></div></div><div class="legend"><span v-for="type in ['up','sideways','triangle','down','transition']" :key="type"><i :style="{background:legendColors[type]}"></i>{{ labels[type] }}</span></div></v-card-text></v-card>
    <v-row v-if="structureResult">
      <v-col cols="12" md="4"><v-card class="summary"><v-card-text><small>主结构状态</small><h2>{{ stateLabel(structureResult.current_state) }}</h2><div class="stats flex-wrap"><span>内部：{{ stateLabel(structureResult.internal_state) }}</span><span>大级别：{{ stateLabel(structureResult.external_state) }}</span><span>阶段：{{ detailLabel(structureResult.state_detail) }}</span><span>ATR {{ Number(structureResult.atr || 0).toFixed(2) }}</span></div><v-alert v-if="structureResult.active_candidate" type="warning" density="compact" variant="tonal" class="mt-3">正在等待{{ structureResult.active_candidate.direction === 'up' ? '向上' : '向下' }}反转确认；K线图以橙色虚线显示候选段</v-alert><v-alert v-if="structureResult.range?.status === 'failed_breakout'" type="info" density="compact" variant="tonal" class="mt-2">价格突破后重新收回区间，当前判定为假突破并恢复原区间</v-alert></v-card-text></v-card></v-col>
      <v-col cols="12" md="8"><v-card class="summary"><v-card-title>结构事件</v-card-title><v-card-text><div class="event-list"><span v-for="(event,index) in recentEvents" :key="`event-${index}`" :class="event.direction==='up'?'event-up':'event-down'">{{ event.type?.toUpperCase() }} · {{ event.direction==='up'?'向上':'向下' }} · {{ event.level ? Number(event.level).toFixed(2) : '流动性扫过' }} · {{ barStamp(event.index) }}</span><span v-if="!recentEvents.length" class="empty">暂无已确认结构事件</span></div></v-card-text></v-card></v-col>
      <v-col cols="12"><v-card class="summary"><v-card-title>多级别结构证据</v-card-title><v-card-text><div class="stats"><span>小级别 Pivot {{ structureResult.structure_levels?.small?.pivot_count || 0 }}</span><span>中级别 Pivot {{ structureResult.structure_levels?.medium?.pivot_count || 0 }}</span><span>大级别 Pivot {{ structureResult.structure_levels?.large?.pivot_count || 0 }}</span><span>HH {{ structureResult.evidence?.higher_highs || 0 }}</span><span>HL {{ structureResult.evidence?.higher_lows || 0 }}</span><span>LH {{ structureResult.evidence?.lower_highs || 0 }}</span><span>LL {{ structureResult.evidence?.lower_lows || 0 }}</span><span>收盘突破 {{ structureResult.evidence?.close_breaks || 0 }}</span><span>影线扫过 {{ structureResult.evidence?.wick_sweeps || 0 }}</span></div></v-card-text></v-card></v-col>
    </v-row>
    <v-row v-if="current">
      <v-col cols="12" md="4"><v-card class="summary"><v-card-text><small>当前结构</small><h2>{{ labels[current.type] || current.type }}</h2><v-chip :color="colors[current.type] || 'grey'" variant="tonal">{{ current.status }}</v-chip><p>{{ current.reason }}</p><div class="stats"><span>持续 {{ current.bars }} 根K线</span><span>结构强度 {{ current.strength ?? current.confidence }}%</span><span v-if="current.evidence">方向一致率 {{ Math.round((current.evidence.direction_ratio || 0) * 100) }}%</span><span v-if="current.evidence">方向效率 {{ Math.round((current.evidence.direction_efficiency || 0) * 100) }}%</span></div></v-card-text></v-card></v-col>
      <v-col cols="12" md="8"><v-card class="summary"><v-card-title>结构变化时间线</v-card-title><v-card-text><div class="timeline"><div v-for="(item,index) in segments" :key="item.id" class="segment" :class="{active:index===segments.length-1}"><i :style="{background:legendColors[item.type]||'#78909c'}"></i><div><strong>{{ labels[item.type] || item.type }}</strong><small>{{ item.start }} → {{ item.end }} · {{ item.bars }} 根</small><p>{{ item.reason }}</p></div></div></div></v-card-text></v-card></v-col>
    </v-row>
    <v-card v-if="segments.length" class="mt-4"><v-card-title>最近 5 个结构段</v-card-title><v-card-text><div class="segment-grid"><article v-for="item in segments" :key="`card-${item.id}`" :class="{active:item.id===current?.id}"><div class="card-head"><v-chip size="small" :color="colors[item.type]||'grey'" variant="tonal">{{ labels[item.type]||item.type }}</v-chip><span>{{ item.bars }} 根</span></div><strong>{{ item.start }} → {{ item.end }}</strong><p>{{ item.reason }}</p><small v-if="item.confirmation">确认于 {{ item.confirmation }}；图形起点与确认时间分开</small><small v-else>当前段尚无独立反转确认时间</small><small class="d-block mt-1">支撑 {{ item.support.toFixed(2) }} · 压力 {{ item.resistance.toFixed(2) }}</small></article></div></v-card-text></v-card>
    <v-card v-else-if="!loading" class="empty mt-4"><v-card-text>暂无足够 K 线识别结构</v-card-text></v-card>
    <v-card v-if="structureResult?.structure_hierarchy" class="mt-4">
      <v-card-title>分层结构</v-card-title>
      <v-card-text><div class="hierarchy-grid"><article v-for="(item,key) in structureResult.structure_hierarchy" :key="key" class="hierarchy-item"><div class="card-head"><strong>{{ hierarchyLabels[key] }}</strong><v-chip size="small" :color="colors[item.bias] || 'grey'" variant="tonal">方向：{{ stateLabel(item.bias) }}</v-chip></div><p>阶段：{{ phaseLabel(item.phase, item.bias) }} · {{ item.pivot_count || 0 }} 个 Pivot</p><small v-if="item.protected_high">保护高点 {{ Number(item.protected_high.price).toFixed(2) }}</small><small v-if="item.protected_low">保护低点 {{ Number(item.protected_low.price).toFixed(2) }}</small><small v-if="item.weak_high">弱高点 {{ Number(item.weak_high.price).toFixed(2) }}</small><small v-if="item.weak_low">弱低点 {{ Number(item.weak_low.price).toFixed(2) }}</small></article></div></v-card-text>
    </v-card>
    <v-card v-if="structureResult?.local_patterns?.length" class="mt-4">
      <v-card-title>局部形态（不覆盖主趋势）</v-card-title>
      <v-card-text><div class="pattern-list"><article v-for="(item,index) in structureResult.local_patterns" :key="`pattern-${index}`"><div class="card-head"><v-chip size="small" color="secondary" variant="tonal">{{ patternLabel(item.type) }}</v-chip><span>{{ patternStatus(item.status) }}</span></div><p v-if="item.start_index != null">覆盖 {{ barStamp(item.start_index) }} → {{ barStamp(item.end_index) }}</p><p v-if="item.high_touches != null">上沿触碰 {{ item.high_touches }} 次 · 下沿触碰 {{ item.low_touches }} 次 · 内部收盘 {{ Math.round((item.inside_ratio || 0) * 100) }}%</p><p v-if="item.breakout">突破方向：{{ item.breakout.direction === 'up' ? '向上' : '向下' }}</p></article></div></v-card-text>
    </v-card>
  </v-container>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { marketAPI } from '../api/market'
import * as echarts from 'echarts'

const route = useRoute(); const symbol = ref(String(route.query.symbol || 'BTCUSD')); const period = ref(String(route.query.period || 'M5')); const periods=['M1','M5','M15','H1','H4']; const symbols=ref([symbol.value]); const loading=ref(false); const error=ref(''); const segments=ref([]); const bars=ref([]); const structureResult=ref(null); const tradePlans=ref([]); const structureReviews=ref([]); const selectedReviewId=ref(''); const chartRef=ref(null); let chart=null; let refreshTimer=null
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
const current=computed(()=>segments.value.at(-1)); async function loadTradePlans(){try{const response=await marketAPI.getStructureTradePlans(symbol.value,period.value);tradePlans.value=Array.isArray(response?.plans)?response.plans:[]}catch(e){tradePlans.value=[]}}
const latestStructureReview=computed(()=>structureReviews.value.find(item=>item.review_id===selectedReviewId.value)||structureReviews.value[0]||null)
async function loadStructureReviews(){try{const response=await marketAPI.getStructureSignalReviews(symbol.value,period.value,30);structureReviews.value=Array.isArray(response?.reviews)?response.reviews:[];if(!structureReviews.value.some(item=>item.review_id===selectedReviewId.value))selectedReviewId.value=structureReviews.value[0]?.review_id||''}catch(e){structureReviews.value=[]}}
const formatPlanTime=value=>value?new Date(Number(value)*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'--'
const modeLabel=value=>value==='live'?'实盘':'模拟盘'
const executionLabel=value=>({unconsumed:'待消费',claimed:'已领取',triggered:'已触发',ordered:'已下单',filled:'已成交',rejected:'已拒绝',expired:'已过期',canceled:'已取消',released:'已释放'}[value]||value||'待消费')
const executionColor=value=>({unconsumed:'warning',claimed:'info',triggered:'info',ordered:'primary',filled:'success',rejected:'error',expired:'grey',canceled:'grey',released:'secondary'}[value]||'grey')
const reviewStatusLabel=value=>({completed:'已完成',skipped:'已跳过',failed:'失败',running:'执行中'}[value]||value||'未知')
const reviewStatusColor=value=>({completed:'success',skipped:'warning',failed:'error',running:'info'}[value]||'grey')
const severityColor=value=>({high:'error',medium:'warning',low:'info'}[value]||'grey')
const hierarchyLabels={internal:'Internal 内部结构',swing:'Swing 主结构',external:'External 外部结构'}
const phaseLabel=(value,bias)=>{if(value==='reversal_confirmed')return bias==='down'?'反转确认后的下跌延续':bias==='up'?'反转确认后的上涨延续':'反转已确认';return {forming:'形成中',continuation:'延续',pullback:'回撤中',reversal_candidate:'反转候选'}[value]||value||'--'}
const patternLabel=value=>({range:'箱体震荡',converging_triangle:'收敛三角形',diverging_triangle:'扩散三角形',ascending_triangle:'上升三角形',descending_triangle:'下降三角形',trendline:'趋势线'}[value]||value||'局部形态')
const patternStatus=value=>({candidate:'候选',confirmed:'已确认',awaiting_breakout:'等待突破',breakout_candidate:'突破候选',breakout_confirmed:'突破已确认',failed_breakout:'假突破',active:'运行中',broken:'已突破'}[value]||value||'--')
// 结构时间轴条已停用，保留空样式函数避免旧模板调用导致渲染中断。
const stripStyle=()=>({display:'none'})
const stateLabel=value=>({up:'上涨趋势',down:'下跌趋势',bullish:'上涨趋势',bearish:'下跌趋势',range:'箱体/三角形',undetermined:'尚未确认'}[value]||'结构过渡')
const localStateValue=result=>String(result?.range?.active?'sideways':(result?.current_state || result?.internal_state || 'undetermined'))
const localStateLabel=result=>stateLabel(localStateValue(result))
const detailLabel=value=>({up:'上涨已确认',down:'下跌已确认',range:'区间已确认',undetermined:'等待建立主结构',up_pullback:'上涨中的回撤',down_pullback:'下跌中的反弹',up_reversal_candidate:'等待向上反转确认',down_reversal_candidate:'等待向下反转确认'}[value]||value||'--')
const barStamp=index=>bars.value[index]?stamp(bars.value[index]):''
const recentEvents=computed(()=>Array.isArray(structureResult.value?.events)?structureResult.value.events.slice(-10).reverse():[])
function renderChartUnsafe(){
  if(!chartRef.value||!bars.value.length)return
  if(chart)chart.dispose(); chart=echarts.init(chartRef.value)
  const rows=bars.value; const result=structureResult.value||{}
  const data=rows.map(x=>[Number(x.open??x.open_price??closeOf(x)),Number(x.close??x.close_price??0),Number(x.low??x.low_price??closeOf(x)),Number(x.high??x.high_price??closeOf(x))])
  const palette=['rgba(73,145,196,.12)','rgba(75,170,123,.12)','rgba(224,163,73,.14)','rgba(207,91,91,.12)','rgba(133,105,190,.12)']
  const area=segments.value.filter(s=>Number.isInteger(s.startIndex)&&Number.isInteger(s.endIndex)&&s.endIndex>=s.startIndex).map((s,i)=>[{xAxis:s.startIndex,itemStyle:{color:palette[i%palette.length]}},{xAxis:s.endIndex,itemStyle:{color:palette[i%palette.length]}}])
  const pivots=Array.isArray(result.swings)?result.swings:[]
  const pivotMarks=pivots.filter(p=>Number.isInteger(p.index)&&p.index>=0&&p.index<rows.length).slice(-24).map(p=>({coord:[p.index,Number(p.price)],value:p.label|| (p.kind==='high'?'H':'L'),name:p.label||p.kind,itemStyle:{color:p.kind==='high'?'#c84f43':'#287a60'}}))
  const eventMarks=(Array.isArray(result.events)?result.events:[]).filter(e=>Number.isInteger(e.index)&&e.index>=0&&e.index<rows.length).map(e=>({value:[e.index,Number(e.level||closeOf(rows[e.index]))],event:e,name:e.type}))
  const eventDefs=[['bos','up','向上 BOS','circle','#16845f'],['bos','down','向下 BOS','circle','#c84f43'],['choch','up','向上 CHoCH','diamond','#16845f'],['choch','down','向下 CHoCH','diamond','#c84f43'],['liquidity_sweep','up','向上扫单','triangle','#16845f'],['liquidity_sweep','down','向下扫单','triangle','#c84f43']]
  const eventSeries=eventDefs.map(([type,direction,name,symbol,color])=>({name,type:'scatter',data:eventMarks.filter(p=>p.event.type===type&&p.event.direction===direction),symbol,symbolRotate:type==='liquidity_sweep'?(direction==='up'?0:180):0,symbolSize:type==='liquidity_sweep'?12:18,itemStyle:{color},label:{show:type!=='liquidity_sweep',formatter:name,color,fontSize:9},z:8}))
  const trend=(Array.isArray(result.trendlines)?result.trendlines:[]).map((line,n)=>({type:'line',name:`${line.kind==='support'?'上涨支撑':'下降压力'} · ${line.level||''}`,data:rows.map((_,i)=>i<line.start_index||i>line.end_index?'-':Number(line.start_price)+(Number(line.slope)||0)*(i-Number(line.start_index))),symbol:'none',lineStyle:{color:line.kind==='support'?'#2b9b72':'#d95d55',type:'dashed',width:2},showSymbol:false,connectNulls:false,z:3}))
  const candidate=result.active_candidate;const candidateSeries=candidate&&Number.isInteger(candidate.swing_index)?[{type:'line',name:'候选结构（未确认）',data:rows.map((row,i)=>i<candidate.swing_index?'-':(i===candidate.swing_index?Number(candidate.level):closeOf(row))),symbol:'none',lineStyle:{color:'#ed9b32',type:'dashed',width:3,opacity:.9},showSymbol:false,connectNulls:true,z:9}]:[]
  const range=result.range?.active?[{name:'箱体上沿',yAxis:Number(result.range.top)},{name:'箱体下沿',yAxis:Number(result.range.bottom)}]:[]
  const eventLines=(Array.isArray(result.events)?result.events:[]).filter(e=>Number.isInteger(e.index)).map(e=>({name:e.type==='choch'?'CHoCH':e.type==='bos'?'BOS':'流动性扫过',xAxis:e.index,lineStyle:{color:e.direction==='up'?'#16845f':'#c84f43',type:'dotted',width:1},label:{show:false}}))
  chart.setOption({animation:false,tooltip:{trigger:'axis',axisPointer:{type:'cross'}},legend:{top:0,type:'scroll',data:['K线',...eventDefs.map(x=>x[2]),...trend.map(x=>x.name),...candidateSeries.map(x=>x.name)]},grid:{left:55,right:35,top:38,bottom:58},xAxis:{type:'category',data:rows.map(stamp),axisLabel:{hideOverlap:true}},yAxis:{scale:true},dataZoom:[{type:'inside'},{type:'slider',height:18,bottom:8}],series:[{name:'K线',type:'candlestick',data,itemStyle:{color:'#1f9d72',color0:'#d95d55',borderColor:'#1f9d72',borderColor0:'#d95d55'},markPoint:{symbol:'circle',symbolSize:9,data:pivotMarks,label:{show:true,position:'top',fontSize:10,formatter:p=>p.value}},markLine:{silent:true,symbol:'none',data:[...range,...eventLines]}},...trend,...candidateSeries,...eventSeries]},true)
  window.addEventListener('resize',resizeChart)
}
function resizeChart(){chart?.resize()}
function safeRenderChart(){try{renderChartUnsafe()}catch(err){console.error('[StructureAnalysis] chart overlay error',err);if(!chartRef.value||!bars.value.length)return;if(chart)chart.dispose();chart=echarts.init(chartRef.value);const data=bars.value.map(x=>[Number(x.open??x.open_price??closeOf(x)),Number(x.close??x.close_price??0),Number(x.low??x.low_price??closeOf(x)),Number(x.high??x.high_price??closeOf(x))]);chart.setOption({animation:false,tooltip:{trigger:'axis'},grid:{left:55,right:35,top:32,bottom:58},xAxis:{type:'category',data:bars.value.map(stamp)},yAxis:{scale:true},dataZoom:[{type:'inside'},{type:'slider',height:18,bottom:8}],series:[{name:'K线',type:'candlestick',data,itemStyle:{color:'#1f9d72',color0:'#d95d55',borderColor:'#1f9d72',borderColor0:'#d95d55'}}]})}}
function renderChart(){safeRenderChart()}
async function load(){loading.value=true;error.value='';try{const res=await marketAPI.getKlines(symbol.value,period.value,600);const raw=Array.isArray(res?.data)?res.data:(Array.isArray(res?.klines)?res.klines:(Array.isArray(res?.results)?res.results:(Array.isArray(res?.data?.klines)?res.data.klines:(Array.isArray(res?.data?.data)?res.data.data:[]))));const now=Date.now()+periodMs(period.value);const filtered=raw.filter(x=>{const t=timeOf(x);return t>0&&t<=now});const rows=(filtered.length?filtered:raw).slice().sort((a,b)=>timeOf(a)-timeOf(b));bars.value=rows;if(!rows.length){error.value=`暂无可用K线（${symbol.value} · ${period.value}）`;return}let backend=null;try{const sr=await marketAPI.getMarketStructure(symbol.value,period.value,600);backend=sr?.data;structureResult.value=backend||null}catch(structureError){structureResult.value=null;error.value='结构分析暂时不可用，已显示原始K线'}if(Array.isArray(backend?.segments)&&backend.segments.length){segments.value=backend.segments.slice(-5).map((s,i)=>{const p=rows.slice(s.start_index,s.end_index+1);const confirmationIndex=s.evidence?.confirmation_index;return {...s,id:`backend-${s.start_index}`,type:s.type,bars:p.length,start:stamp(p[0]),end:stamp(p.at(-1)),confirmation:Number.isInteger(confirmationIndex)&&rows[confirmationIndex]?stamp(rows[confirmationIndex]):'',support:Math.min(...p.map(x=>Number(x.low??x.low_price??closeOf(x)))),resistance:Math.max(...p.map(x=>Number(x.high??x.high_price??closeOf(x)))),confidence:s.strength??70,strength:s.strength??70,status:s.locked?'已确认并锁定':(s.status==='candidate'?'等待确认':'当前已确认'),reason:s.reason||'结构证据已计算'}})}else segments.value=build(rows);await nextTick();renderChart()}catch(e){error.value=e?.response?.data?.detail||'K线数据加载失败'}finally{loading.value=false}}
async function loadSymbols(){try{const res=await marketAPI.getSymbols();const values=(res?.symbols||res?.data||[]).map(item=>typeof item==='string'?item:(item.symbol||item.value||'')).filter(Boolean);symbols.value=Array.from(new Set([symbol.value,...values]))}catch(e){/* 保留当前品种，行情接口失败不阻断页面 */}}
watch(period,()=>{load();loadTradePlans();loadStructureReviews()});watch(symbol,()=>{load();loadTradePlans();loadStructureReviews()});onMounted(async()=>{await loadSymbols();await load();await loadTradePlans();await loadStructureReviews();refreshTimer=setInterval(()=>{load();loadTradePlans();loadStructureReviews()},30000)});onUnmounted(()=>{if(refreshTimer)clearInterval(refreshTimer);window.removeEventListener('resize',resizeChart);chart?.dispose()})
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
