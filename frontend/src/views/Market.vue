<template>
  <v-container fluid>
    <v-row>
      <v-col cols="12">
        <h1 class="mb-4">行情分析</h1>
      </v-col>
    </v-row>

    <!-- 最新快讯 -->
    <v-row v-if="latestFlashNews">
      <v-col cols="12">
        <v-alert
          :type="latestFlashNews.importance >= 2 ? 'warning' : 'info'"
          dense
          class="mb-2"
          dismissible
          @input="latestFlashNews = null"
        >
          <div class="d-flex align-center">
            <v-icon small class="mr-2">mdi-lightning-bolt</v-icon>
            <v-chip v-if="latestFlashNews.speaker" color="primary" x-small class="mr-2">
              {{ latestFlashNews.speaker }}
            </v-chip>
            <span class="text-body-2">{{ latestFlashNews.content }}</span>
            <v-spacer></v-spacer>
            <span class="text-caption grey--text ml-2">{{ formatNewsTime(latestFlashNews.time) }}</span>
          </div>
          <!-- 影响分析 -->
          <div v-if="latestFlashNews.impact && Object.keys(latestFlashNews.impact).length > 0" class="mt-1">
            <v-chip
              v-for="(impact, symbol) in latestFlashNews.impact"
              :key="symbol"
              :color="getImpactColor(impact.direction)"
              x-small
              class="mr-1"
            >
              {{ symbol }}: {{ impact.direction }}
            </v-chip>
          </div>
        </v-alert>
      </v-col>
    </v-row>

    <!-- 转折点提醒通知 -->
    <v-row v-if="pivotAlerts.length > 0">
      <v-col cols="12">
        <v-alert
          v-for="(alert, index) in pivotAlerts"
          :key="index"
          :type="alert.direction === 'high' ? 'warning' : 'info'"
          dismissible
          class="mb-2"
          @input="removeAlert(index)"
        >
          <div class="d-flex flex-wrap align-center">
            <strong>{{ alert.symbol }}</strong>
            <v-chip small :color="alert.direction === 'high' ? 'error' : 'success'" class="ml-2">
              接近{{ alert.direction === 'high' ? '高点' : '低点' }}
            </v-chip>
            <v-chip small :color="getPivotPeriodColor(alert.period)" class="ml-2">
              {{ alert.period }}
            </v-chip>
          </div>
          <div class="mt-1">
            <span class="text-caption">转折点: <strong>{{ alert.pivot_price }}</strong></span>
            <span class="text-caption ml-4">当前价格: <strong>{{ alert.current_price }}</strong></span>
            <span class="text-caption ml-4">距离: <strong>{{ (alert.distance_pct * 100).toFixed(2) }}%</strong></span>
          </div>

          <!-- 显示各周期信息（如果有合并数据） -->
          <div v-if="alert.periods && alert.periods.length > 0" class="mt-2">
            <v-chip
              v-for="p in alert.periods"
              :key="p.period"
              x-small
              class="mr-1 mb-1"
              :color="getPivotPeriodColor(p.period)"
            >
              {{ p.period }}: {{ p.price }} ({{ p.distance_pct }}%)
            </v-chip>
          </div>

          <!-- 如果有待确认订单，显示订单信息和操作按钮 -->
          <div v-if="alert.pending_order" class="mt-3 pa-2 grey lighten-4 rounded">
            <div class="text-subtitle-2 font-weight-bold mb-2">
              <v-icon small color="primary" class="mr-1">mdi-file-document-edit</v-icon>
              自动生成交易指令
            </div>

            <!-- AI与技术信号比较 -->
            <div v-if="alert.pending_order.ai_driven" class="mb-2">
              <div class="d-flex align-center mb-1">
                <span class="text-caption mr-2">技术信号:</span>
                <v-chip :color="alert.pending_order.tech_action === '买入' ? 'success' : 'error'" x-small>
                  {{ alert.pending_order.tech_action }}
                </v-chip>
                <span class="text-caption mx-2">AI建议:</span>
                <v-chip :color="alert.pending_order.ai_direction === '买入' ? 'success' : 'error'" x-small>
                  {{ alert.pending_order.ai_direction }}
                </v-chip>
              </div>
            </div>

            <!-- AI冲突警告 -->
            <v-alert v-if="alert.pending_order.ai_conflict" type="warning" dense class="mb-2">
              <div class="d-flex align-center">
                <v-icon small class="mr-1">mdi-alert-circle</v-icon>
                <strong>AI与技术信号冲突！以AI方向为准</strong>
              </div>
              <div v-if="alert.pending_order.ai_reason" class="text-caption mt-1">
                AI理由: {{ alert.pending_order.ai_reason }}
              </div>
            </v-alert>

            <!-- AI一致提示 -->
            <v-alert v-if="alert.pending_order.ai_aligned" type="success" dense class="mb-2">
              <div class="d-flex align-center">
                <v-icon small class="mr-1">mdi-check-circle</v-icon>
                <strong>AI与技术信号一致</strong>
              </div>
              <div v-if="alert.pending_order.ai_reason" class="text-caption mt-1">
                AI理由: {{ alert.pending_order.ai_reason }}
              </div>
            </v-alert>

            <div class="mb-2">
              <v-chip :color="alert.pending_order.action === 'b' ? 'success' : 'error'" x-small class="mr-2">
                {{ alert.pending_order.action === 'b' ? '买入' : '卖出' }}
              </v-chip>
              <span class="mr-3">价格: {{ alert.pending_order.price?.toFixed(2) }}</span>
            </div>
            <!-- 可编辑字段 -->
            <v-row dense class="mb-2">
              <v-col cols="3">
                <v-text-field
                  v-model.number="alert.pending_order.mount"
                  label="手数"
                  type="number"
                  step="0.01"
                  min="0.01"
                  dense
                  hide-details
                  outlined
                ></v-text-field>
              </v-col>
              <v-col cols="3">
                <v-text-field
                  v-model.number="alert.pending_order.sl"
                  label="止损"
                  type="number"
                  step="0.01"
                  dense
                  hide-details
                  outlined
                ></v-text-field>
              </v-col>
              <v-col cols="3">
                <v-text-field
                  v-model.number="alert.pending_order.tp"
                  label="止盈"
                  type="number"
                  step="0.01"
                  dense
                  hide-details
                  outlined
                  :hint="alert.pending_order.ai_aligned ? 'AI建议' : ''"
                  persistent-hint
                ></v-text-field>
              </v-col>
              <v-col cols="3" class="d-flex align-center">
                <v-btn
                  color="success"
                  small
                  class="mr-1"
                  :loading="confirmingOrderId === alert.pending_order.order_id"
                  @click="confirmAlertOrder(alert.pending_order, index)"
                >
                  <v-icon left small>mdi-check</v-icon>
                  确认
                </v-btn>
                <v-btn
                  color="error"
                  small
                  outlined
                  :loading="rejectingOrderId === alert.pending_order.order_id"
                  @click="rejectAlertOrder(alert.pending_order.order_id, index)"
                >
                  放弃
                </v-btn>
              </v-col>
            </v-row>
            <div class="text-caption grey--text mb-1">
              {{ alert.pending_order.reason }}
            </div>
            <div class="text-caption grey--text">
              <v-icon small>mdi-clock-outline</v-icon>
              3分钟内未操作将自动移除
            </div>
          </div>
        </v-alert>
      </v-col>
    </v-row>

    <!-- AI入场价提醒通知 -->
    <v-row v-if="aiEntryAlerts.length > 0">
      <v-col cols="12">
        <v-alert
          v-for="(alert, index) in aiEntryAlerts"
          :key="'ai-'+index"
          type="info"
          dismissible
          class="mb-2"
          @input="removeAiEntryAlert(index)"
        >
          <div class="d-flex flex-wrap align-center">
            <v-icon small color="white" class="mr-1">mdi-robot</v-icon>
            <strong>{{ alert.symbol }} {{ alert.period }}</strong>
            <span class="ml-2">AI建议入场</span>
            <v-chip small class="ml-2" :color="alert.direction === 'buy' ? 'success' : 'error'">
              {{ alert.direction === 'buy' ? '买入' : '卖出' }}
            </v-chip>
            <span class="ml-2">入场价: {{ alert.entry_price }}</span>
            <span class="ml-2">当前价: {{ alert.current_price }}</span>
            <span class="ml-2">差距: {{ alert.price_diff_pct }}%</span>
          </div>

          <!-- 如果有待确认订单，显示订单信息和操作按钮 -->
          <div v-if="alert.pending_order" class="mt-3 pa-2 grey lighten-4 rounded">
            <div class="text-subtitle-2 font-weight-bold mb-2">
              <v-icon small color="primary" class="mr-1">mdi-file-document-edit</v-icon>
              AI交易建议
            </div>

            <div class="mb-2">
              <v-chip :color="alert.pending_order.action === 'b' ? 'success' : 'error'" x-small class="mr-2">
                {{ alert.pending_order.action === 'b' ? '买入' : '卖出' }}
              </v-chip>
              <span class="mr-3">入场价: {{ alert.pending_order.price?.toFixed(2) }}</span>
              <span class="mr-3">止损: {{ alert.pending_order.sl }}</span>
              <span class="mr-3">止盈: {{ alert.pending_order.tp }}</span>
            </div>

            <!-- 可编辑字段 -->
            <v-row dense class="mb-2">
              <v-col cols="3">
                <v-text-field
                  v-model.number="alert.pending_order.mount"
                  label="手数"
                  type="number"
                  step="0.01"
                  min="0.01"
                  dense
                  hide-details
                  outlined
                ></v-text-field>
              </v-col>
              <v-col cols="3">
                <v-text-field
                  v-model.number="alert.pending_order.sl"
                  label="止损"
                  type="number"
                  step="0.01"
                  dense
                  hide-details
                  outlined
                ></v-text-field>
              </v-col>
              <v-col cols="3">
                <v-text-field
                  v-model.number="alert.pending_order.tp"
                  label="止盈"
                  type="number"
                  step="0.01"
                  dense
                  hide-details
                  outlined
                ></v-text-field>
              </v-col>
              <v-col cols="3" class="d-flex align-center">
                <v-btn
                  color="success"
                  small
                  class="mr-1"
                  :loading="confirmingOrderId === alert.pending_order.order_id"
                  @click="confirmAiEntryOrder(alert.pending_order, index)"
                >
                  <v-icon left small>mdi-check</v-icon>
                  确认
                </v-btn>
                <v-btn
                  color="error"
                  small
                  outlined
                  :loading="rejectingOrderId === alert.pending_order.order_id"
                  @click="rejectAiEntryOrder(alert.pending_order.order_id, index)"
                >
                  放弃
                </v-btn>
              </v-col>
            </v-row>
            <div class="text-caption grey--text mb-1">
              <v-icon small class="mr-1">mdi-lightbulb</v-icon>
              {{ alert.reason }}
            </div>
            <div class="text-caption grey--text">
              <v-icon small>mdi-clock-outline</v-icon>
              3分钟内未操作将自动移除
            </div>
          </div>
        </v-alert>
      </v-col>
    </v-row>

    <!-- 关键点位订单提醒 -->
    <v-row v-if="keyLevelAlerts.length > 0">
      <v-col cols="12">
        <v-alert
          v-for="(alert, index) in keyLevelAlerts"
          :key="'key-'+index"
          type="success"
          dismissible
          class="mb-2"
          @input="removeKeyLevelAlert(index)"
        >
          <div class="d-flex flex-wrap align-center">
            <v-icon small color="white" class="mr-1">mdi-chart-line</v-icon>
            <strong>{{ alert.symbol }}</strong>
            <v-chip small :color="alert.action === 'b' ? 'success' : 'error'" class="ml-2">
              {{ alert.action_text }}
            </v-chip>
            <span class="ml-2">关键点位策略</span>
          </div>
          <div class="mt-1">
            <span class="text-caption">关键位: <strong>{{ alert.key_level }}</strong></span>
            <span class="text-caption ml-4">入场价: <strong>{{ alert.price?.toFixed(2) }}</strong></span>
            <span class="text-caption ml-4">距离: <strong>{{ alert.distance_pct }}%</strong></span>
          </div>

          <!-- 如果有待确认订单，显示订单信息和操作按钮 -->
          <div v-if="alert.pending_order" class="mt-3 pa-2 grey lighten-4 rounded">
            <div class="text-subtitle-2 font-weight-bold mb-2">
              <v-icon small color="primary" class="mr-1">mdi-file-document-edit</v-icon>
              关键点位交易建议
            </div>

            <div class="mb-2">
              <v-chip :color="alert.pending_order.action === 'b' ? 'success' : 'error'" x-small class="mr-2">
                {{ alert.pending_order.action === 'b' ? '买入' : '卖出' }}
              </v-chip>
              <span class="mr-3">入场价: {{ alert.pending_order.price?.toFixed(2) }}</span>
              <span class="mr-3">止损: {{ alert.pending_order.sl }}</span>
              <span class="mr-3">止盈: {{ alert.pending_order.tp }}</span>
            </div>

            <!-- AI方向对比 -->
            <div class="mb-2 pa-2 white rounded">
              <div class="text-caption font-weight-bold mb-1">
                <v-icon small class="mr-1">mdi-robot</v-icon>
                AI方向对比
              </div>
              <div v-if="alert.pending_order.ai_directions && Object.keys(alert.pending_order.ai_directions).length > 0" class="d-flex flex-wrap align-center">
                <span class="text-caption mr-2">关键点位: <strong :class="alert.pending_order.action === 'b' ? 'success--text' : 'error--text'">{{ alert.pending_order.key_level_direction_text }}</strong></span>
                <template v-for="(dirInfo, period) in alert.pending_order.ai_directions" :key="period">
                  <v-chip
                    :color="dirInfo.direction === 'buy' ? 'success' : 'error'"
                    x-small
                    outlined
                    class="mr-1"
                  >
                    {{ period }}: {{ dirInfo.text }}
                  </v-chip>
                </template>
              </div>
              <div v-else class="text-caption grey--text">
                关键点位: <strong :class="alert.pending_order.action === 'b' ? 'success--text' : 'error--text'">{{ alert.pending_order.key_level_direction_text }}</strong>
                <span class="ml-2">AI暂无分析数据</span>
              </div>
              <div v-if="alert.pending_order.recommendation" class="mt-1">
                <v-chip
                  :color="alert.pending_order.recommendation_color || 'grey'"
                  x-small
                  dark
                >
                  <v-icon x-small left>mdi-lightbulb</v-icon>
                  {{ alert.pending_order.recommendation }}
                </v-chip>
              </div>
            </div>

            <!-- 可编辑字段 -->
            <v-row dense class="mb-2">
              <v-col cols="3">
                <v-text-field
                  v-model.number="alert.pending_order.mount"
                  label="手数"
                  type="number"
                  step="0.01"
                  min="0.01"
                  dense
                  hide-details
                  outlined
                ></v-text-field>
              </v-col>
              <v-col cols="3">
                <v-text-field
                  v-model.number="alert.pending_order.sl"
                  label="止损"
                  type="number"
                  step="0.01"
                  dense
                  hide-details
                  outlined
                ></v-text-field>
              </v-col>
              <v-col cols="3">
                <v-text-field
                  v-model.number="alert.pending_order.tp"
                  label="止盈"
                  type="number"
                  step="0.01"
                  dense
                  hide-details
                  outlined
                ></v-text-field>
              </v-col>
              <v-col cols="3" class="d-flex align-center">
                <v-btn
                  color="success"
                  small
                  class="mr-1"
                  :loading="confirmingOrderId === alert.pending_order.order_id"
                  @click="confirmKeyLevelOrder(alert.pending_order, index)"
                >
                  <v-icon left small>mdi-check</v-icon>
                  确认
                </v-btn>
                <v-btn
                  color="error"
                  small
                  outlined
                  :loading="rejectingOrderId === alert.pending_order.order_id"
                  @click="rejectKeyLevelOrder(alert.pending_order.order_id, index)"
                >
                  放弃
                </v-btn>
              </v-col>
            </v-row>
            <div class="text-caption grey--text mb-1">
              <v-icon small class="mr-1">mdi-lightbulb</v-icon>
              {{ alert.reason }}
            </div>
            <div class="text-caption grey--text">
              <v-icon small>mdi-clock-outline</v-icon>
              3分钟内未操作将自动移除
            </div>
          </div>
        </v-alert>
      </v-col>
    </v-row>

    <!-- WebSocket 状态 -->
    <v-row>
      <v-col cols="12">
        <v-chip
          :color="wsConnected ? 'success' : 'error'"
          small
          class="mr-2"
        >
          <v-icon left small>mdi-lan-connect</v-icon>
          {{ wsConnected ? 'WebSocket 已连接' : 'WebSocket 断开' }}
        </v-chip>
      </v-col>
    </v-row>

    <!-- 大模型趋势分析 -->
    <v-row>
      <v-col cols="12">
        <v-card>
          <v-card-title>
            <v-icon class="mr-2">mdi-robot</v-icon>
            AI趋势分析
            <v-spacer></v-spacer>
            <!-- 分析状态指示器 -->
            <v-chip v-if="llmAnalyzing" small color="primary" class="mr-2">
              <v-progress-circular indeterminate size="12" width="2" class="mr-1"></v-progress-circular>
              {{ llmAnalysisStatus || '分析中...' }}
            </v-chip>
            <v-chip v-else-if="llmStatus.enabled" small color="success" class="mr-2">已启用</v-chip>
            <v-chip v-else small color="grey">未配置</v-chip>
          </v-card-title>
          <v-card-text>
            <!-- 分析时间 -->
            <div v-if="llmStatus.last_analysis_time" class="text-caption grey--text mb-3">
              上次分析: {{ llmStatus.last_analysis_time }}
            </div>

            <!-- 无数据提示 -->
            <div v-if="!llmAnalysis || Object.keys(llmAnalysis).length === 0" class="text-center py-6">
              <v-icon size="48" color="grey lighten-1">mdi-robot-outline</v-icon>
              <p class="mt-3 grey--text">
                <template v-if="llmAnalyzing">
                  {{ llmAnalysisStatus || '正在分析...' }}
                </template>
                <template v-else>
                  {{ llmStatus.enabled ? '等待分析完成...' : '请先配置大模型API' }}
                </template>
              </p>
              <!-- 分析进度条 -->
              <v-progress-linear v-if="llmAnalyzing" indeterminate color="primary" class="mt-2"></v-progress-linear>
            </div>

            <!-- 分析结果 -->
            <div v-else>
              <v-expansion-panels>
                <v-expansion-panel v-for="(data, symbol) in llmAnalysis" :key="symbol">
                  <v-expansion-panel-header>
                    <div class="d-flex align-center">
                      <strong class="mr-3">{{ symbol }}</strong>
                      <v-chip
                        v-if="data.analysis && data.analysis.overall_trend"
                        :color="getTrendChipColor(data.analysis.overall_trend.direction)"
                        small
                      >
                        {{ data.analysis.overall_trend.direction }}
                      </v-chip>
                      <!-- 休市状态 -->
                      <v-chip
                        v-if="data.market_status === 'closed'"
                        color="grey"
                        small
                        class="ml-2"
                      >
                        <v-icon left x-small>mdi-pause-circle</v-icon>
                        休市中
                      </v-chip>
                      <!-- 数据未更新 -->
                      <v-chip
                        v-else-if="data.data_stale"
                        color="warning"
                        small
                        class="ml-2"
                      >
                        <v-icon left x-small>mdi-alert</v-icon>
                        数据未更新
                      </v-chip>
                    </div>
                  </v-expansion-panel-header>
                  <v-expansion-panel-content>
                    <!-- 休市提示 -->
                    <v-alert
                      v-if="data.market_status === 'closed'"
                      type="info"
                      dense
                      class="mb-3"
                    >
                      <div class="d-flex align-center">
                        <v-icon small class="mr-2">mdi-pause-circle</v-icon>
                        <span>
                          休市中，暂无行情数据。下次开市时将自动更新分析。
                        </span>
                      </div>
                    </v-alert>
                    <!-- 数据过期提示 -->
                    <v-alert
                      v-else-if="data.data_stale"
                      type="warning"
                      dense
                      class="mb-3"
                    >
                      <div class="d-flex align-center">
                        <v-icon small class="mr-2">mdi-clock-alert</v-icon>
                        <span>
                          行情数据已 {{ data.stale_seconds || '?' }} 秒未更新，当前显示上次分析结果。
                          <span class="text-caption">({{ data.analyzed_at }})</span>
                        </span>
                      </div>
                    </v-alert>

                    <!-- 各周期趋势（休市时可能没有分析结果）-->
                    <div v-if="data.analysis && data.analysis.trend_analysis" class="mb-4">
                      <div class="text-subtitle-2 mb-2">各周期趋势</div>
                      <v-simple-table dense>
                        <template v-slot:default>
                          <thead>
                            <tr>
                              <th>周期</th>
                              <th>AI趋势</th>
                              <th>置信度</th>
                              <th>AI说明</th>
                              <th>技术趋势</th>
                              <th>技术说明</th>
                              <th>结论</th>
                            </tr>
                          </thead>
                          <tbody>
                            <tr v-for="(trend, period) in data.analysis.trend_analysis" :key="period">
                              <td><strong>{{ period }}</strong></td>
                              <td>
                                <v-chip :color="getTrendChipColor(trend.trend)" x-small>
                                  {{ trend.trend }}
                                </v-chip>
                              </td>
                              <td>
                                <span class="text-caption">{{ trend.confidence }}%</span>
                              </td>
                              <td class="text-caption grey--text" style="max-width: 180px;">
                                {{ trend.reason }}
                              </td>
                              <td>
                                <v-chip
                                  v-if="getTechTrend(symbol, period)"
                                  :color="getTrendColor(getTechTrend(symbol, period).trend)"
                                  x-small
                                >
                                  {{ getTrendLabel(getTechTrend(symbol, period).trend) }}
                                </v-chip>
                                <span v-else class="grey--text text-caption">-</span>
                              </td>
                              <td class="text-caption grey--text" style="max-width: 180px;">
                                <div v-if="getTechTrend(symbol, period)" :title="getTechTrend(symbol, period).reason">
                                  {{ getTechTrend(symbol, period).reason }}
                                </div>
                                <span v-else>-</span>
                              </td>
                              <td>
                                <v-chip
                                  :color="getConclusionColor(symbol, period, trend)"
                                  x-small
                                >
                                  {{ getConclusion(symbol, period, trend) }}
                                </v-chip>
                              </td>
                            </tr>
                          </tbody>
                        </template>
                      </v-simple-table>
                    </div>

                    <!-- 关键价位 -->
                    <div v-if="data.analysis && data.analysis.key_levels" class="mb-4">
                      <div class="text-subtitle-2 mb-2">关键价位</div>
                      <v-row>
                        <v-col cols="6">
                          <div class="text-caption grey--text">压力位</div>
                          <div v-for="(level, i) in data.analysis.key_levels.resistance" :key="'r'+i">
                            <v-chip color="error" x-small class="mr-1">{{ level }}</v-chip>
                          </div>
                        </v-col>
                        <v-col cols="6">
                          <div class="text-caption grey--text">支撑位</div>
                          <div v-for="(level, i) in data.analysis.key_levels.support" :key="'s'+i">
                            <v-chip color="success" x-small class="mr-1">{{ level }}</v-chip>
                          </div>
                        </v-col>
                      </v-row>
                    </div>

                    <!-- 交易建议 -->
                    <div v-if="data.analysis && data.analysis.trade_suggestions && data.analysis.trade_suggestions.length > 0">
                      <div class="text-subtitle-2 mb-2">交易建议</div>
                      <v-simple-table dense>
                        <template v-slot:default>
                          <thead>
                            <tr>
                              <th>周期</th>
                              <th>方向</th>
                              <th>入场价</th>
                              <th>止损</th>
                              <th>止盈</th>
                              <th>理由</th>
                            </tr>
                          </thead>
                          <tbody>
                            <tr v-for="(suggestion, i) in data.analysis.trade_suggestions" :key="i">
                              <td>{{ suggestion.period }}</td>
                              <td>
                                <v-chip :color="suggestion.direction === 'buy' ? 'success' : 'error'" x-small>
                                  {{ suggestion.direction === 'buy' ? '买入' : '卖出' }}
                                </v-chip>
                              </td>
                              <td>{{ suggestion.entry_price }}</td>
                              <td>{{ suggestion.stop_loss }}</td>
                              <td>{{ suggestion.take_profit }}</td>
                              <td class="text-caption">{{ suggestion.reason }}</td>
                            </tr>
                          </tbody>
                        </template>
                      </v-simple-table>
                    </div>

                    <div class="text-caption grey--text mt-2">
                      分析时间: {{ data.analyzed_at }}
                    </div>
                  </v-expansion-panel-content>
                </v-expansion-panel>
              </v-expansion-panels>
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 错误提示 -->
    <v-snackbar v-model="showError" color="error" timeout="5000">
      {{ errorMessage }}
    </v-snackbar>
  </v-container>
</template>

<script>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { marketAPI } from '@/api/market'

export default {
  name: 'Market',
  setup() {
    // 数据
    const allPivots = ref([])
    const marketStatus = ref({})
    const thresholds = ref({})
    const pivotAlerts = ref([])
    const aiEntryAlerts = ref([])
    const keyLevelAlerts = ref([])  // 关键点位订单提醒
    const loading = ref(false)
    const showError = ref(false)
    const errorMessage = ref('')

    // 趋势分析相关
    const trendData = ref({})
    const loadingTrend = ref(false)
    const pendingOrders = ref([])

    // 订单确认/放弃状态
    const confirmingOrderId = ref(null)
    const rejectingOrderId = ref(null)

    // WebSocket
    const ws = ref(null)
    const wsConnected = ref(false)

    // 大模型分析
    const llmStatus = ref({ enabled: false })
    const llmAnalysis = ref({})
    const llmAnalyzing = ref(false)
    const llmAnalysisStatus = ref('')

    // 最新快讯
    const latestFlashNews = ref(null)
    const newsWs = ref(null)

    // 计算属性
    const highPivots = computed(() => {
      return allPivots.value
        .filter(p => p.direction === 'high')
        .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))
        .slice(0, 20)
    })

    const lowPivots = computed(() => {
      return allPivots.value
        .filter(p => p.direction === 'low')
        .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))
        .slice(0, 20)
    })

    // 方法
    const loadStatus = async () => {
      try {
        const status = await marketAPI.getStatus()
        marketStatus.value = status
      } catch (err) {
        console.error('加载状态失败:', err)
      }
    }

    const loadThresholds = async () => {
      try {
        const data = await marketAPI.getThresholds()
        thresholds.value = data.thresholds || {}
      } catch (err) {
        console.error('加载阈值失败:', err)
      }
    }

    const connectWebSocket = () => {
      ws.value = marketAPI.createWebSocket(
        // onMessage
        (data) => {
          if (data.type === 'pivot_alert') {
            // 添加新的提醒到列表顶部
            pivotAlerts.value.unshift(data)
            // 只保留最近10条
            if (pivotAlerts.value.length > 10) {
              pivotAlerts.value.pop()
            }
          } else if (data.type === 'ai_entry_alert') {
            // AI入场价提醒
            console.log('收到AI入场价提醒:', data)
            aiEntryAlerts.value.unshift(data)
            // 只保留最近10条
            if (aiEntryAlerts.value.length > 10) {
              aiEntryAlerts.value.pop()
            }
          } else if (data.type === 'key_level_alert') {
            // 关键点位订单提醒
            console.log('收到关键点位订单提醒:', data)
            keyLevelAlerts.value.unshift(data)
            // 只保留最近10条
            if (keyLevelAlerts.value.length > 10) {
              keyLevelAlerts.value.pop()
            }
          } else if (data.type === 'connected') {
            wsConnected.value = true
          } else if (data.type === 'llm_analysis_status') {
            // 大模型分析状态更新（流式）
            console.log('收到分析状态更新:', data)
            if (data.status === 'analyzing' || data.status === 'streaming') {
              llmAnalyzing.value = true
              llmAnalysisStatus.value = data.message
            } else if (data.status === 'stale') {
              // 数据过期，停止加载状态
              llmAnalyzing.value = false
              llmAnalysisStatus.value = data.message
            } else if (data.status === 'error') {
              llmAnalyzing.value = false
              llmAnalysisStatus.value = data.message
            }
          } else if (data.type === 'llm_analysis_update') {
            // 大模型分析更新，刷新分析结果
            console.log('收到大模型分析更新通知:', data)
            llmAnalyzing.value = false
            llmAnalysisStatus.value = ''
            loadLLMAnalysis()
            loadLLMStatus()
            loadTrend()
          }
        },
        // onError
        () => {
          wsConnected.value = false
        },
        // onOpen
        () => {
          wsConnected.value = true
        },
        // onClose
        () => {
          wsConnected.value = false
          // 5秒后重连
          setTimeout(() => {
            if (!wsConnected.value) {
              connectWebSocket()
            }
          }, 5000)
        }
      )
    }

    const getThresholdLabel = (period) => {
      const threshold = thresholds.value[period]
      if (threshold) {
        return `±${threshold.percent}`
      }
      return period
    }

    const getPivotPeriodColor = (period) => {
      const colors = {
        'H4': 'red',
        'H1': 'orange',
        'M15': 'yellow darken-2',
        'M5': 'green',
        'M1': 'blue'
      }
      return colors[period] || 'grey'
    }

    const getTechTrend = (symbol, period) => {
      const normalized = symbol.replace('#', '').toUpperCase()
      const symbolVariants = [normalized, normalized + '#', symbol]
      for (const s of symbolVariants) {
        // 后端返回的数据结构是 resonance.periods
        if (trendData.value[s]?.resonance?.periods?.[period]) {
          return trendData.value[s].resonance.periods[period]
        }
      }
      return null
    }

    // 判断AI趋势和技术趋势是否冲突
    const hasConflict = (symbol, period, aiTrend) => {
      const techTrend = getTechTrend(symbol, period)
      if (!techTrend) return false

      const aiDir = getTrendDirection(aiTrend.trend)
      const techDir = techTrend.trend

      // 如果一个看涨一个看跌，则是冲突
      if ((aiDir === 'up' && techDir === 'down') || (aiDir === 'down' && techDir === 'up')) {
        return true
      }
      return false
    }

    // 获取趋势方向
    const getTrendDirection = (trend) => {
      if (!trend) return 'unknown'
      const t = trend.toLowerCase()
      if (t.includes('上涨') || t.includes('上升') || t === 'up') return 'up'
      if (t.includes('下跌') || t.includes('下降') || t === 'down') return 'down'
      return 'sideways'
    }

    // 获取结论
    const getConclusion = (symbol, period, aiTrend) => {
      const techTrend = getTechTrend(symbol, period)
      const aiDir = getTrendDirection(aiTrend.trend)
      const techDir = techTrend?.trend || 'unknown'

      if (hasConflict(symbol, period, aiTrend)) {
        return '谨慎观望'
      }

      if (aiDir === techDir) {
        if (aiDir === 'up') return '看涨'
        if (aiDir === 'down') return '看跌'
        return '震荡'
      }

      // AI有判断但技术分析无明确趋势
      if (techDir === 'sideways' || techDir === 'unknown') {
        return aiDir === 'up' ? '偏多' : aiDir === 'down' ? '偏空' : '震荡'
      }

      return '待观察'
    }

    // 获取结论颜色
    const getConclusionColor = (symbol, period, aiTrend) => {
      if (hasConflict(symbol, period, aiTrend)) {
        return 'warning'
      }
      const conclusion = getConclusion(symbol, period, aiTrend)
      if (conclusion === '看涨') return 'success'
      if (conclusion === '看跌') return 'error'
      if (conclusion === '偏多') return 'success lighten-2'
      if (conclusion === '偏空') return 'error lighten-2'
      return 'grey'
    }

    // 趋势分析相关方法
    const loadTrend = async () => {
      // 加载所有已分析品种的趋势数据
      const symbols = Object.keys(llmAnalysis.value)
      if (symbols.length === 0) return

      loadingTrend.value = true
      try {
        // 并行加载所有品种的趋势
        const promises = symbols.map(symbol => marketAPI.getTrend(symbol))
        const results = await Promise.all(promises)

        // 合并结果
        results.forEach((data, index) => {
          if (data) {
            const symbol = symbols[index]
            trendData.value[symbol] = data
            console.log(`[Market] 加载 ${symbol} 技术趋势:`, data.resonance?.signal)
          }
        })
      } catch (err) {
        console.error('加载趋势分析失败:', err)
      } finally {
        loadingTrend.value = false
      }
    }

    const loadPendingOrders = async () => {
      try {
        const data = await marketAPI.getPendingOrders()
        pendingOrders.value = data.orders || []
      } catch (err) {
        console.error('加载待确认订单失败:', err)
      }
    }

    // 大模型分析相关
    const loadLLMStatus = async () => {
      try {
        const data = await marketAPI.getLLMStatus()
        if (data.status === 'ok') {
          llmStatus.value = data.data
        }
      } catch (err) {
        console.error('获取大模型状态失败:', err)
      }
    }

    const loadLLMAnalysis = async () => {
      try {
        const data = await marketAPI.getLLMAnalysis()
        if (data.status === 'ok') {
          llmAnalysis.value = data.data || {}
        }
      } catch (err) {
        console.error('获取大模型分析失败:', err)
      }
    }

    // 获取最新快讯
    const fetchLatestFlashNews = async () => {
      try {
        const response = await fetch('/api/news/flash?count=1')
        const data = await response.json()
        if (data.status === 'ok' && data.data && data.data.length > 0) {
          latestFlashNews.value = data.data[0]
        }
      } catch (err) {
        console.error('获取快讯失败:', err)
      }
    }

    // 连接新闻WebSocket
    const connectNewsWebSocket = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const wsUrl = `${protocol}//${window.location.host}/api/news/ws`

      newsWs.value = new WebSocket(wsUrl)

      newsWs.value.onopen = () => {
        console.log('[Market] 新闻WebSocket已连接')
      }

      newsWs.value.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          if (data.type === 'flash_news' && data.news) {
            // 更新最新快讯
            latestFlashNews.value = data.news
          } else if (data.type === 'connected') {
            // 连接成功
            console.log('[Market] 新闻WebSocket连接确认')
          }
        } catch (err) {
          console.error('[Market] 解析新闻WebSocket消息失败:', err)
        }
      }

      newsWs.value.onerror = (err) => {
        console.error('[Market] 新闻WebSocket错误:', err)
      }

      newsWs.value.onclose = () => {
        console.log('[Market] 新闻WebSocket断开，5秒后重连...')
        setTimeout(() => {
          if (!newsWs.value || newsWs.value.readyState === WebSocket.CLOSED) {
            connectNewsWebSocket()
          }
        }, 5000)
      }
    }

    // 格式化快讯时间
    const formatNewsTime = (timeStr) => {
      if (!timeStr) return ''
      const date = new Date(timeStr)
      return date.toLocaleString('zh-CN', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      })
    }

    // 获取影响颜色
    const getImpactColor = (direction) => {
      if (direction === '利好') return 'success'
      if (direction === '利空') return 'error'
      return 'info'
    }

    const getTrendChipColor = (trend) => {
      if (!trend) return 'grey'
      const trendLower = trend.toLowerCase()
      if (trendLower.includes('上涨') || trendLower === 'up' || trendLower === 'buy') {
        return 'success'
      }
      if (trendLower.includes('下跌') || trendLower === 'down' || trendLower === 'sell') {
        return 'error'
      }
      return 'warning'
    }

    const confirmOrder = async (orderId) => {
      try {
        const data = await marketAPI.confirmOrder(orderId)
        if (data.status === 'ok') {
          await loadPendingOrders()
        } else {
          errorMessage.value = data.message || '确认订单失败'
          showError.value = true
        }
      } catch (err) {
        errorMessage.value = `确认订单失败: ${err.message}`
        showError.value = true
      }
    }

    const rejectOrder = async (orderId) => {
      try {
        const data = await marketAPI.rejectOrder(orderId)
        if (data.status === 'ok') {
          await loadPendingOrders()
        } else {
          errorMessage.value = data.message || '拒绝订单失败'
          showError.value = true
        }
      } catch (err) {
        errorMessage.value = `拒绝订单失败: ${err.message}`
        showError.value = true
      }
    }

    // 从提醒列表中移除
    const removeAlert = (index) => {
      pivotAlerts.value.splice(index, 1)
    }

    // 确认提醒中的订单（可修改参数）
    const confirmAlertOrder = async (order, alertIndex) => {
      confirmingOrderId.value = order.order_id
      try {
        // 发送修改后的订单数据
        const data = await marketAPI.confirmOrderWithUpdate(order.order_id, {
          mount: order.mount,
          sl: order.sl,
          tp: order.tp
        })
        if (data.status === 'ok') {
          // 移除该提醒
          pivotAlerts.value.splice(alertIndex, 1)
          await loadPendingOrders()
        } else {
          errorMessage.value = data.message || '确认订单失败'
          showError.value = true
        }
      } catch (err) {
        errorMessage.value = `确认订单失败: ${err.message}`
        showError.value = true
      } finally {
        confirmingOrderId.value = null
      }
    }

    // 放弃提醒中的订单
    const rejectAlertOrder = async (orderId, alertIndex) => {
      rejectingOrderId.value = orderId
      try {
        const data = await marketAPI.rejectOrder(orderId)
        if (data.status === 'ok') {
          // 移除该提醒
          pivotAlerts.value.splice(alertIndex, 1)
          await loadPendingOrders()
        } else {
          errorMessage.value = data.message || '放弃订单失败'
          showError.value = true
        }
      } catch (err) {
        errorMessage.value = `放弃订单失败: ${err.message}`
        showError.value = true
      } finally {
        rejectingOrderId.value = null
      }
    }

    // AI入场价提醒操作
    const removeAiEntryAlert = (index) => {
      aiEntryAlerts.value.splice(index, 1)
    }

    const confirmAiEntryOrder = async (order, alertIndex) => {
      confirmingOrderId.value = order.order_id
      try {
        const data = await marketAPI.confirmOrderWithUpdate(order.order_id, {
          mount: order.mount,
          sl: order.sl,
          tp: order.tp
        })
        if (data.status === 'ok') {
          aiEntryAlerts.value.splice(alertIndex, 1)
          await loadPendingOrders()
        } else {
          errorMessage.value = data.message || '确认订单失败'
          showError.value = true
        }
      } catch (err) {
        errorMessage.value = `确认订单失败: ${err.message}`
        showError.value = true
      } finally {
        confirmingOrderId.value = null
      }
    }

    const rejectAiEntryOrder = async (orderId, alertIndex) => {
      rejectingOrderId.value = orderId
      try {
        const data = await marketAPI.rejectOrder(orderId)
        if (data.status === 'ok') {
          aiEntryAlerts.value.splice(alertIndex, 1)
          await loadPendingOrders()
        } else {
          errorMessage.value = data.message || '放弃订单失败'
          showError.value = true
        }
      } catch (err) {
        errorMessage.value = `放弃订单失败: ${err.message}`
        showError.value = true
      } finally {
        rejectingOrderId.value = null
      }
    }

    // 关键点位订单提醒操作
    const removeKeyLevelAlert = (index) => {
      keyLevelAlerts.value.splice(index, 1)
    }

    const confirmKeyLevelOrder = async (order, alertIndex) => {
      confirmingOrderId.value = order.order_id
      try {
        const data = await marketAPI.confirmOrderWithUpdate(order.order_id, {
          mount: order.mount,
          sl: order.sl,
          tp: order.tp
        })
        if (data.status === 'ok') {
          keyLevelAlerts.value.splice(alertIndex, 1)
          await loadPendingOrders()
        } else {
          errorMessage.value = data.message || '确认订单失败'
          showError.value = true
        }
      } catch (err) {
        errorMessage.value = `确认订单失败: ${err.message}`
        showError.value = true
      } finally {
        confirmingOrderId.value = null
      }
    }

    const rejectKeyLevelOrder = async (orderId, alertIndex) => {
      rejectingOrderId.value = orderId
      try {
        const data = await marketAPI.rejectOrder(orderId)
        if (data.status === 'ok') {
          keyLevelAlerts.value.splice(alertIndex, 1)
          await loadPendingOrders()
        } else {
          errorMessage.value = data.message || '放弃订单失败'
          showError.value = true
        }
      } catch (err) {
        errorMessage.value = `放弃订单失败: ${err.message}`
        showError.value = true
      } finally {
        rejectingOrderId.value = null
      }
    }

    const getTrendColor = (trend) => {
      if (trend === 'up') return 'success'
      if (trend === 'down') return 'error'
      return 'warning'
    }

    const getTrendLabel = (trend) => {
      if (trend === 'up') return '上升'
      if (trend === 'down') return '下降'
      if (trend === 'sideways') return '震荡'
      return '未知'
    }

    // 生命周期
    onMounted(() => {
      loadStatus()
      loadThresholds()
      loadPendingOrders()
      loadLLMStatus()
      loadLLMAnalysis()
      connectWebSocket()
      // 快讯相关
      fetchLatestFlashNews()
      connectNewsWebSocket()

      // 定时刷新
      const statusInterval = setInterval(() => {
        loadStatus()
        loadPendingOrders()
        loadLLMAnalysis()
        loadTrend()
      }, 10000)

      // 清理
      onUnmounted(() => {
        clearInterval(statusInterval)
        if (ws.value) {
          ws.value.close()
        }
        if (newsWs.value) {
          newsWs.value.close()
        }
      })
    })

    return {
      allPivots,
      highPivots,
      lowPivots,
      marketStatus,
      thresholds,
      pivotAlerts,
      loading,
      showError,
      errorMessage,
      wsConnected,
      getThresholdLabel,
      getPivotPeriodColor,
      // 趋势分析相关
      trendData,
      loadingTrend,
      pendingOrders,
      loadTrend,
      confirmOrder,
      rejectOrder,
      getTrendColor,
      getTrendLabel,
      // 提醒订单操作
      confirmingOrderId,
      rejectingOrderId,
      removeAlert,
      confirmAlertOrder,
      rejectAlertOrder,
      // AI入场价提醒
      aiEntryAlerts,
      removeAiEntryAlert,
      confirmAiEntryOrder,
      rejectAiEntryOrder,
      // 关键点位订单提醒
      keyLevelAlerts,
      removeKeyLevelAlert,
      confirmKeyLevelOrder,
      rejectKeyLevelOrder,
      // 大模型分析
      llmStatus,
      llmAnalysis,
      llmAnalyzing,
      llmAnalysisStatus,
      loadLLMStatus,
      loadLLMAnalysis,
      getTrendChipColor,
      // 技术分析与AI分析整合
      getTechTrend,
      hasConflict,
      getConclusion,
      getConclusionColor,
      // 快讯相关
      latestFlashNews,
      formatNewsTime,
      getImpactColor
    }
  }
}
</script>

<style scoped>
.v-card {
  margin-bottom: 16px;
}

.trend-card {
  height: 100%;
}

.reason-text {
  font-size: 11px;
  color: #666;
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
</style>