<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { statisticsApi } from '@/api/statistics'
import type {
  TokenOverview,
  TokenTrendItem,
  TokenByFlowItem,
  TokenByModelItem,
  TokenStatisticsQuery
} from '@/types/statistics'
import * as echarts from 'echarts'

const loading = ref(false)
const overview = ref<TokenOverview>({
  total_prompt_tokens: 0,
  total_completion_tokens: 0,
  total_tokens: 0,
  llm_call_count: 0
})
const trendData = ref<TokenTrendItem[]>([])
const flowData = ref<TokenByFlowItem[]>([])
const modelData = ref<TokenByModelItem[]>([])

const queryParams = reactive<TokenStatisticsQuery>({
  start_date: '',
  end_date: '',
  time_grain: 'day'
})

const grainOptions = [
  { label: '按天', value: 'day' },
  { label: '按周', value: 'week' },
  { label: '按月', value: 'month' }
]

let trendChart: echarts.ECharts | null = null
let flowChart: echarts.ECharts | null = null
let modelChart: echarts.ECharts | null = null

function formatNumber(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K'
  return String(n)
}

async function loadData() {
  loading.value = true
  try {
    const [overviewRes, trendRes, flowRes, modelRes] = await Promise.all([
      statisticsApi.tokenOverview(queryParams),
      statisticsApi.tokenTrend(queryParams),
      statisticsApi.tokenByFlow(queryParams),
      statisticsApi.tokenByModel(queryParams)
    ])
    if (overviewRes.data.code === 1) overview.value = overviewRes.data.data
    if (trendRes.data.code === 1) trendData.value = trendRes.data.data
    if (flowRes.data.code === 1) flowData.value = flowRes.data.data
    if (modelRes.data.code === 1) modelData.value = modelRes.data.data
    scheduleRender()
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  loadData()
}

function handleReset() {
  queryParams.start_date = ''
  queryParams.end_date = ''
  queryParams.time_grain = 'day'
  handleSearch()
}

// ---- ECharts ----

function initChart(id: string, chart: echarts.ECharts | null): echarts.ECharts | null {
  const el = document.getElementById(id)
  if (!el || el.clientWidth === 0 || el.clientHeight === 0) return chart
  const c = chart ?? echarts.init(el)
  c.resize()
  return c
}

function renderCharts() {
  trendChart = initChart('trend-chart', trendChart)
  flowChart = initChart('flow-chart', flowChart)
  modelChart = initChart('model-chart', modelChart)
  if (trendChart) setTrendOption(trendChart)
  if (flowChart) setFlowOption(flowChart)
  if (modelChart) setModelOption(modelChart)
}

function setTrendOption(chart: echarts.ECharts) {
  chart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { data: ['输入 Token', '输出 Token', '总 Token'], bottom: 0 },
    grid: { left: 60, right: 30, top: 20, bottom: 40 },
    xAxis: { type: 'category', data: trendData.value.map(i => i.date), boundaryGap: false },
    yAxis: { type: 'value' },
    series: [
      {
        name: '输入 Token',
        type: 'line',
        areaStyle: { opacity: 0.3 },
        smooth: true,
        data: trendData.value.map(i => i.prompt_tokens)
      },
      {
        name: '输出 Token',
        type: 'line',
        areaStyle: { opacity: 0.3 },
        smooth: true,
        data: trendData.value.map(i => i.completion_tokens)
      },
      {
        name: '总 Token',
        type: 'line',
        smooth: true,
        data: trendData.value.map(i => i.total_tokens)
      }
    ]
  })
}

function setFlowOption(chart: echarts.ECharts) {
  const sorted = [...flowData.value].sort((a, b) => a.total_tokens - b.total_tokens)
  chart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: ['输入', '输出'], bottom: 0 },
    grid: { left: 120, right: 30, top: 10, bottom: 40 },
    xAxis: { type: 'value' },
    yAxis: {
      type: 'category',
      data: sorted.map(i => i.flow_name || `#${i.flow_id}`),
      axisLabel: { width: 100, overflow: 'truncate' }
    },
    series: [
      {
        name: '输入',
        type: 'bar',
        stack: 'total',
        data: sorted.map(i => i.prompt_tokens)
      },
      {
        name: '输出',
        type: 'bar',
        stack: 'total',
        data: sorted.map(i => i.completion_tokens)
      }
    ]
  })
}

function setModelOption(chart: echarts.ECharts) {
  const sorted = [...modelData.value].sort((a, b) => a.total_tokens - b.total_tokens)
  chart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: ['输入', '输出', '缓存读取', '缓存写入', '推理'], bottom: 0 },
    grid: { left: 140, right: 30, top: 10, bottom: 60 },
    xAxis: { type: 'value' },
    yAxis: {
      type: 'category',
      data: sorted.map(i => i.model || i.provider || 'unknown'),
      axisLabel: { width: 120, overflow: 'truncate' }
    },
    series: [
      { name: '输入', type: 'bar', stack: 'total', data: sorted.map(i => i.prompt_tokens) },
      { name: '输出', type: 'bar', stack: 'total', data: sorted.map(i => i.completion_tokens) },
      { name: '缓存读取', type: 'bar', stack: 'total', data: sorted.map(i => i.cache_read_tokens) },
      {
        name: '缓存写入',
        type: 'bar',
        stack: 'total',
        data: sorted.map(i => i.cache_write_tokens)
      },
      { name: '推理', type: 'bar', stack: 'total', data: sorted.map(i => i.reasoning_tokens) }
    ]
  })
}

function handleResize() {
  trendChart?.resize()
  flowChart?.resize()
  modelChart?.resize()
}

let resizeObserver: ResizeObserver | null = null
let pendingRender = false

function scheduleRender() {
  if (pendingRender) return
  pendingRender = true
  requestAnimationFrame(() => {
    pendingRender = false
    renderCharts()
  })
}

onMounted(() => {
  loadData()
  window.addEventListener('resize', handleResize)
  setTimeout(() => {
    const trendEl = document.getElementById('trend-chart')
    const flowEl = document.getElementById('flow-chart')
    const modelEl = document.getElementById('model-chart')
    resizeObserver = new ResizeObserver(() => scheduleRender())
    if (trendEl?.parentElement) resizeObserver.observe(trendEl.parentElement)
    if (flowEl?.parentElement) resizeObserver.observe(flowEl.parentElement)
    if (modelEl?.parentElement) resizeObserver.observe(modelEl.parentElement)
    renderCharts()
  }, 200)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  resizeObserver?.disconnect()
  trendChart?.dispose()
  flowChart?.dispose()
  modelChart?.dispose()
})
</script>

<template>
  <div class="token-statistics-page page">
    <div class="page-header">
      <h1 class="page-title">Token 统计</h1>
    </div>

    <el-form :inline="true" class="search-bar" @submit.prevent="handleSearch">
      <el-form-item label="开始日期">
        <el-date-picker
          v-model="queryParams.start_date"
          type="date"
          placeholder="开始日期"
          value-format="YYYY-MM-DD"
          clearable
          style="width: 160px"
        />
      </el-form-item>
      <el-form-item label="结束日期">
        <el-date-picker
          v-model="queryParams.end_date"
          type="date"
          placeholder="结束日期"
          value-format="YYYY-MM-DD"
          clearable
          style="width: 160px"
        />
      </el-form-item>
      <el-form-item label="粒度">
        <el-select v-model="queryParams.time_grain" style="width: 100px">
          <el-option
            v-for="opt in grainOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button class="btn-search" :icon="Refresh" @click="handleSearch">查询</el-button>
        <el-button class="btn-reset" @click="handleReset">重置</el-button>
      </el-form-item>
    </el-form>

    <div v-loading="loading" class="content-area">
      <!-- 概览卡片 -->
      <div class="overview-cards">
        <div class="overview-card">
          <div class="card-value">{{ formatNumber(overview.total_tokens) }}</div>
          <div class="card-label">总 Token</div>
        </div>
        <div class="overview-card">
          <div class="card-value">{{ formatNumber(overview.total_prompt_tokens) }}</div>
          <div class="card-label">输入 Token</div>
        </div>
        <div class="overview-card">
          <div class="card-value">{{ formatNumber(overview.total_completion_tokens) }}</div>
          <div class="card-label">输出 Token</div>
        </div>
        <div class="overview-card">
          <div class="card-value">{{ overview.llm_call_count }}</div>
          <div class="card-label">LLM 调用</div>
        </div>
      </div>

      <!-- 趋势 -->
      <div class="card-panel chart-section">
        <h3 class="section-title">Token 消耗趋势</h3>
        <div id="trend-chart" class="chart-container"></div>
      </div>

      <!-- 按流程统计 -->
      <div class="card-panel chart-section">
        <h3 class="section-title">按流程/Agent 统计</h3>
        <div id="flow-chart" class="chart-container"></div>
      </div>

      <div class="card-panel table-container">
        <el-table v-loading="loading" :data="flowData" stripe style="width: 100%">
          <el-table-column prop="flow_name" label="名称" min-width="160" show-overflow-tooltip />
          <el-table-column prop="flow_type" label="类型" width="80">
            <template #default="{ row }">
              <span
                class="type-badge"
                :class="row.flow_type === 'agent' ? 'type-agent' : 'type-flow'"
              >
                {{ row.flow_type === 'agent' ? 'Agent' : 'Flow' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column prop="prompt_tokens" label="输入 Token" width="120" align="right">
            <template #default="{ row }">{{ formatNumber(row.prompt_tokens) }}</template>
          </el-table-column>
          <el-table-column prop="completion_tokens" label="输出 Token" width="120" align="right">
            <template #default="{ row }">{{ formatNumber(row.completion_tokens) }}</template>
          </el-table-column>
          <el-table-column prop="total_tokens" label="总 Token" width="120" align="right">
            <template #default="{ row }">{{ formatNumber(row.total_tokens) }}</template>
          </el-table-column>
          <el-table-column prop="call_count" label="LLM 调用" width="110" align="right" />
        </el-table>
      </div>

      <!-- 按模型统计 -->
      <div class="card-panel chart-section">
        <h3 class="section-title">按模型统计</h3>
        <div id="model-chart" class="chart-container chart-container-lg"></div>
      </div>

      <div class="card-panel table-container">
        <el-table v-loading="loading" :data="modelData" stripe style="width: 100%">
          <el-table-column prop="model" label="模型" min-width="180" show-overflow-tooltip />
          <el-table-column prop="provider" label="Provider" width="120" show-overflow-tooltip />
          <el-table-column prop="prompt_tokens" label="输入" width="110" align="right">
            <template #default="{ row }">{{ formatNumber(row.prompt_tokens) }}</template>
          </el-table-column>
          <el-table-column prop="completion_tokens" label="输出" width="110" align="right">
            <template #default="{ row }">{{ formatNumber(row.completion_tokens) }}</template>
          </el-table-column>
          <el-table-column prop="total_tokens" label="总 Token" width="110" align="right">
            <template #default="{ row }">{{ formatNumber(row.total_tokens) }}</template>
          </el-table-column>
          <el-table-column prop="call_count" label="调用" width="80" align="right" />
          <el-table-column prop="cache_read_tokens" label="缓存读" width="100" align="right">
            <template #default="{ row }">
              <span :class="{ 'cache-tag': row.cache_read_tokens > 0 }">
                {{ formatNumber(row.cache_read_tokens) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column prop="cache_write_tokens" label="缓存写" width="100" align="right">
            <template #default="{ row }">
              <span :class="{ 'cache-tag': row.cache_write_tokens > 0 }">
                {{ formatNumber(row.cache_write_tokens) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column prop="reasoning_tokens" label="推理" width="100" align="right">
            <template #default="{ row }">
              <span :class="{ 'reasoning-tag': row.reasoning_tokens > 0 }">
                {{ formatNumber(row.reasoning_tokens) }}
              </span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.token-statistics-page {
  overflow-y: auto;
}
.overview-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}

.table-container {
  margin-bottom: 20px;
}

.overview-card {
  background: #fff;
  border-radius: 12px;
  padding: 20px;
  text-align: center;
  border: 1px solid #e2e8f0;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

.card-value {
  font-size: 26px;
  font-weight: 700;
  color: #0f172a;
}

.card-label {
  font-size: 13px;
  color: #64748b;
  margin-top: 6px;
}

.chart-section {
  margin-bottom: 20px;
  overflow: visible !important;
}

.chart-container {
  width: 100%;
  height: 350px;
}

.chart-container-lg {
  height: 450px;
}

.type-badge {
  display: inline-flex;
  padding: 2px 8px;
  font-size: 10px;
  font-weight: 700;
  border-radius: 9999px;
}

.type-agent {
  background: #ecfdf5;
  color: #059669;
}

.type-flow {
  background: #eff6ff;
  color: #2563eb;
}

.cache-tag {
  color: #059669;
}

.reasoning-tag {
  color: #7c3aed;
}
</style>
