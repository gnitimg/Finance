<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { buildChartData } from './chart-data.js'

let chartRuntimePromise = null
function chartRuntime() {
  if (!chartRuntimePromise) {
    chartRuntimePromise = Promise.all([
      import('echarts/core'), import('echarts/charts'), import('echarts/components'), import('echarts/renderers'),
    ]).then(([core, charts, components, renderers]) => {
      core.use([
        charts.BarChart, charts.LineChart, charts.ScatterChart,
        components.DataZoomComponent, components.GridComponent, components.LegendComponent,
        components.MarkAreaComponent, components.TooltipComponent, renderers.CanvasRenderer,
      ])
      return core
    })
  }
  return chartRuntimePromise
}

const periods = [
  { label: '1D', range: '1d', interval: '5m' },
  { label: '1W', range: '5d', interval: '15m' },
  { label: '1M', range: '1mo', interval: '1d' },
  { label: '3M', range: '3mo', interval: '1d' },
  { label: '6M', range: '6mo', interval: '1d' },
  { label: '1Y', range: '1y', interval: '1d' },
]

const marketOptions = [
  { value: 'auto', code: 'AUTO', label: '自动识别' },
  { value: 'cn', code: 'CN', label: 'A 股' },
  { value: 'hk', code: 'HK', label: '港股' },
  { value: 'us', code: 'US', label: '美股' },
  { value: 'etf', code: 'ETF', label: 'ETF' },
  { value: 'fund', code: 'FUND', label: '基金' },
  { value: 'future', code: 'FUT', label: '期货' },
  { value: 'metal', code: 'METAL', label: '金银商品' },
  { value: 'crypto', code: 'Crypto', label: '数字资产' },
]

const monitorCategories = [
  { value: 'potential', label: '潜力', description: '上行前瞻与偏强结构' },
  { value: 'risk', label: '风险', description: '下行前瞻与弱势结构' },
  { value: 'anomaly', label: '异动', description: '价格或量能异常' },
]
const defaultWatchlist = [
  { market: 'us', symbol: 'NVDA', categories: ['potential', 'risk', 'anomaly'] },
  { market: 'us', symbol: 'AAPL', categories: ['potential', 'risk', 'anomaly'] },
  { market: 'hk', symbol: '00700', categories: ['potential', 'risk', 'anomaly'] },
  { market: 'cn', symbol: '601619', categories: ['potential', 'risk', 'anomaly'] },
  { market: 'crypto', symbol: 'USDT', categories: ['risk', 'anomaly'] },
  { market: 'etf', symbol: 'SPY', categories: ['potential', 'risk', 'anomaly'] },
  { market: 'metal', symbol: 'GOLD', categories: ['potential', 'risk', 'anomaly'] },
]
const defaultThresholds = { forecast: 0.7, price: 2.0, volume: 1.8 }
const storageKeys = { watchlist: 'gnitimg.finance.watchlist.v1', thresholds: 'gnitimg.finance.thresholds.v1', notifications: 'gnitimg.finance.notifications.v1', timezone: 'gnitimg.finance.timezone.v1', fullForecast: 'gnitimg.finance.full-forecast.v1' }
const browserTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
const timeZoneOptions = [
  { value: 'auto', label: '跟随浏览器', detail: browserTimeZone },
  { value: 'UTC', label: '协调世界时', detail: 'UTC' },
  { value: 'Asia/Shanghai', label: '中国市场', detail: 'Asia / Shanghai' },
  { value: 'Asia/Hong_Kong', label: '香港市场', detail: 'Asia / Hong Kong' },
  { value: 'America/New_York', label: '美国东部', detail: 'America / New York' },
]

function stored(key, fallback) {
  try {
    const value = JSON.parse(window.localStorage.getItem(key))
    return value ?? fallback
  } catch { return fallback }
}

const state = reactive({
  market: 'us', symbol: 'NVDA', period: periods[3], analysis: null,
  overview: [], health: null, loading: true, modelLoading: false, live: false, monitoring: false,
  lastSync: null, monitorAt: null, streamRefresh: 5, stablecoin: null, news: null,
})
const searchMarket = ref('auto')
const searchSymbol = ref('NVDA')
const marketMenuOpen = ref(false)
const chartEl = ref(null)
const watchlist = ref(stored(storageKeys.watchlist, defaultWatchlist))
const thresholds = reactive({ ...defaultThresholds, ...stored(storageKeys.thresholds, {}) })
const notifications = ref(stored(storageKeys.notifications, []))
const watchlistOpen = ref(false)
const notificationOpen = ref(false)
const activeAlert = ref(null)
const alertQueue = ref([])
const draftWatchlist = ref([])
const draftMarket = ref('us')
const draftSymbol = ref('')
const draftThresholds = reactive({ ...thresholds })
const storedTimeZone = stored(storageKeys.timezone, 'auto')
const timeZoneMode = ref(timeZoneOptions.some((item) => item.value === storedTimeZone) ? storedTimeZone : 'auto')
const draftTimeZone = ref(timeZoneMode.value)
const fullForecast = ref(Boolean(stored(storageKeys.fullForecast, false)))
const draftFullForecast = ref(fullForecast.value)
const marketClock = ref('--/-- --:--:--')
const clockSynced = ref(false)
const toast = reactive({ visible: false, message: '', type: 'error' })
const settingsSection = ref('timezone')
const settingsSections = [
  { id: 'timezone', index: '01', label: '显示时区' },
  { id: 'forecast', index: '02', label: '预测轨迹' },
  { id: 'watchlist', index: '03', label: '自选与预警' },
  { id: 'health', index: '04', label: '数据源健康' },
  { id: 'about', index: '05', label: '关于' },
]
let chart = null
let stream = null
let selectedTimer = null
let quoteTimer = null
let monitorTimer = null
let clockTimer = null
let clockSyncTimer = null
let toastTimer = null
let monitorBusy = false
let quoteBusy = false
let loadSequence = 0
let clockOffsetMs = 0

const data = computed(() => state.analysis?.data || {})
const asset = computed(() => data.value.asset || {})
const quote = computed(() => data.value.quote || {})
const technical = computed(() => data.value.technical || {})
const forecast = computed(() => data.value.ml_forecast || {})
const marketSentiment = computed(() => data.value.market_sentiment || state.news?.market_sentiment || {})
const backtestStats = ref({ hits: 0, total: 0 })
const changeClass = computed(() => Number(quote.value.change_pct || 0) >= 0 ? 'positive' : 'negative')
const openChangePct = computed(() => {
  const open = Number(quote.value.open)
  const price = Number(quote.value.price)
  if (!Number.isFinite(open) || open <= 0 || !Number.isFinite(price) || price <= 0) return null
  return (price / open - 1) * 100
})
const providerSummary = computed(() => Object.entries(state.health?.data?.providers || {}))
const selectedMarketOption = computed(() => marketOptions.find((option) => option.value === searchMarket.value) || marketOptions[0])
const unreadCount = computed(() => notifications.value.filter((item) => !item.read).length)
const tickerDuration = computed(() => Math.max(14, state.overview.length * 3.5))
const activeTimeZone = computed(() => timeZoneMode.value === 'auto' ? browserTimeZone : timeZoneMode.value)

function number(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(Number(value))
}
function compact(value) {
  if (value === null || value === undefined) return '—'
  return new Intl.NumberFormat('zh-CN', { notation: 'compact', maximumFractionDigits: 2 }).format(Number(value))
}
function currency(value) {
  const code = asset.value.currency || 'USD'
  if (value === null || value === undefined) return '—'
  try { return new Intl.NumberFormat('zh-CN', { style: 'currency', currency: code, maximumFractionDigits: code === 'USD' && asset.value.market === 'crypto' ? 4 : 2 }).format(value) }
  catch { return `${number(value)} ${code}` }
}
function dateTime(value) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', { timeZone: activeTimeZone.value, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(value))
}
function marketLabel(value) {
  return ({ us: 'US', hk: 'HK', cn: 'CN', crypto: 'CRYPTO', etf: 'ETF', fund: 'FUND', future: 'FUT', metal: 'METAL' })[value] || String(value || '').toUpperCase()
}
function profileLabel(value) {
  return ({ cn_equity: 'A 股个体模型', hk_equity: '港股个体模型', us_equity: '美股个体模型', etf: 'ETF 模型', fund: '基金模型', future: '期货模型', metal: '贵金属模型', crypto: '数字资产模型', stablecoin: '稳定币模型' })[value] || '市场自适应模型'
}
function stanceLabel(value) {
  return ({ bullish: '偏强', bearish: '偏弱', mixed: '信号交错' })[value] || '数据积累中'
}
function gradeLabel(value) {
  return ({ high: '校准较好', medium: '校准一般', low: '校准有限' })[value] || '样本不足'
}
function phaseLabel(value) {
  if (value === null || value === undefined) return '样本不足'
  const lag = Number(value)
  if (lag === 0) return '同步'
  return lag > 0 ? `领先 ${lag} BAR` : `滞后 ${Math.abs(lag)} BAR`
}
function validationLabel(value) {
  if (value === true) return '通过'
  if (value === false) return '未跑赢基线'
  return '样本不足'
}
function forecastStateLabel() {
  if (!forecast.value.next_forecast) return '样本积累中'
  if (forecast.value.ensemble?.latest_regime_guarded) return '反转闸门已改写短线方向'
  if (forecast.value.evaluation?.validation_passed === false) return '未通过验证，预测已降幅'
  if (Number(forecast.value.evaluation?.phase_lag_bars || 0) < 0) return '存在相位滞后，不参与预警'
  return '样本外验证通过'
}
function sentimentLabel(value) {
  return ({ bullish: '偏多', bearish: '偏空', neutral: '中性' })[value] || '等待证据'
}
function signalLabel(value) {
  const text = String(value || '')
  const price = text.match(/^Price ([\d.]+)% (above|below) MA20$/)
  if (price) return `价格较 MA20 ${price[2] === 'above' ? '高' : '低'} ${price[1]}%`
  const rsi = text.match(/^RSI 14 at ([\d.]+)$/)
  if (rsi) return `RSI 14 当前为 ${rsi[1]}`
  const macd = text.match(/^MACD histogram ([-\d.]+)$/)
  if (macd) return `MACD 柱值 ${macd[1]}`
  const volume = text.match(/^Relative volume ([\d.]+)×$/)
  if (volume) return Number(volume[1]) > 0 ? `相对成交量 ${volume[1]}×` : '相对成交量形成中'
  return text
}
function levelLabel(value) {
  return ({ '20-bar low': '20 BAR 低点', '20-bar high': '20 BAR 高点' })[value] || value
}
function chartAxisLabel(value, index, chartData) {
  const raw = String(value || '')
  const current = new Date(raw)
  if (Number.isNaN(current.getTime())) return ''
  const isFuture = chartData.future.timestamps?.includes(raw)
  const formatter = new Intl.DateTimeFormat('en-CA', { timeZone: activeTimeZone.value, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
  const parts = Object.fromEntries(formatter.formatToParts(current).filter((item) => item.type !== 'literal').map((item) => [item.type, item.value]))
  const { year, month, day, hour, minute } = parts
  if (state.period.interval === '1d') {
    const label = state.period.range === '1y' ? `${year}/${month}/${day}` : `${month}/${day}`
    return isFuture ? `{forecast|${label}}` : label
  }
  const previous = index > 0 ? new Date(chartData.axis[index - 1]) : null
  const previousParts = previous ? Object.fromEntries(formatter.formatToParts(previous).filter((item) => item.type !== 'literal').map((item) => [item.type, item.value])) : null
  const newSession = !previousParts || `${previousParts.year}-${previousParts.month}-${previousParts.day}` !== `${year}-${month}-${day}`
  const label = newSession ? `${month}/${day}\n${hour}:${minute}` : `${hour}:${minute}`
  return isFuture ? `{forecast|${label}}` : label
}
function chartTooltip(params, chartData) {
  const points = Array.isArray(params) ? params : [params]
  const axisValue = String(points[0]?.axisValue || '')
  const isFuture = chartData.future.timestamps?.includes(axisValue)
  const title = isFuture ? `模型前瞻 · ${dateTime(axisValue)}` : dateTime(axisValue)
  const rows = points
    .filter((point) => point.value !== null && point.value !== undefined && !['成交量', '前瞻区间下界', '前瞻不确定区间'].includes(point.seriesName))
    .map((point) => `${point.marker}${point.seriesName}<strong style="float:right;margin-left:24px">${number(point.value, Number(point.value) < 10 ? 3 : 2)}</strong>`)
  const interval = chartData.future.intervals?.[axisValue]
  if (interval) rows.push(`<span style="color:#9588c8">模型区间</span><strong style="float:right;margin-left:24px">${number(interval.lower, interval.lower < 10 ? 3 : 2)} – ${number(interval.upper, interval.upper < 10 ? 3 : 2)}</strong>`)
  return [`<div style="margin-bottom:8px;color:#aeb7ad">${title}</div>`, ...rows].join('<br>')
}
function safeUrl(value) {
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol) ? url.href : '#'
  } catch { return '#' }
}

function quoteEpoch(value) {
  const time = Date.parse(String(value || ''))
  return Number.isFinite(time) ? time : 0
}

// Several loaders merge quote payloads produced by different cache layers;
// a stale layer must never drag the displayed price or chart backwards.
function isFreshQuote(incoming, current) {
  return quoteEpoch(incoming?.as_of) >= quoteEpoch(current?.as_of)
}

function categoryLabel(value) {
  return ({ potential: '潜力', risk: '风险', anomaly: '异动' })[value] || '监测'
}
function monitorStatus(item) {
  const selected = watchlist.value.find((entry) => entry.market === item.asset?.market && entry.symbol === item.asset?.symbol)
  const allowed = new Set(selected?.categories || [])
  const priority = { risk: 3, anomaly: 2, potential: 1 }
  const match = (item.matches || []).filter((entry) => allowed.has(entry.category)).sort((left, right) => priority[right.category] - priority[left.category] || right.score - left.score)[0]
  if (!match) return { category: 'normal', label: '监测中', tone: 'neutral' }
  return {
    category: match.category,
    label: ({ risk: '风险预警', anomaly: '异动', potential: '潜力观察' })[match.category],
    tone: ({ risk: 'negative', anomaly: 'warning', potential: 'positive' })[match.category],
  }
}
function friendlyError(message) {
  const text = String(message || '')
  if (/404|no data|not found|unsupported symbol/i.test(text)) return '没有找到该标的，请检查代码或选择正确市场'
  if (/timed out|timeout/i.test(text)) return '数据源响应超时，请稍后重试'
  if (/429|rate/i.test(text)) return '数据源请求繁忙，请稍后重试'
  return '暂时无法取得该标的数据'
}
function showToast(message, type = 'error') {
  window.clearTimeout(toastTimer)
  toast.message = message
  toast.type = type
  toast.visible = true
  toastTimer = window.setTimeout(() => { toast.visible = false }, 3_000)
}
function updateUtcClock() {
  const current = new Date(Date.now() + clockOffsetMs)
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-GB', { timeZone: activeTimeZone.value, timeZoneName: 'short', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).formatToParts(current).filter((item) => item.type !== 'literal').map((item) => [item.type, item.value]))
  marketClock.value = `${parts.timeZoneName || 'UTC'} ${parts.month}/${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`
}
async function syncClock() {
  const started = Date.now()
  try {
    const payload = await fetchJson('/api/time')
    const finished = Date.now()
    clockOffsetMs = Number(payload.epoch_ms) - (started + finished) / 2
    clockSynced.value = true
    updateUtcClock()
  } catch { clockSynced.value = false }
}
function persistNotifications() {
  notifications.value = notifications.value.slice(0, 100)
  window.localStorage.setItem(storageKeys.notifications, JSON.stringify(notifications.value))
}
function alertIsEnabled(alert) {
  const entry = watchlist.value.find((item) => item.market === alert.asset?.market && item.symbol === alert.asset?.symbol)
  return Boolean(entry?.categories?.includes(alert.category))
}
function showNextAlert() {
  if (!activeAlert.value) activeAlert.value = alertQueue.value.shift() || null
}
function ingestAlerts(alerts = []) {
  const existing = new Map(notifications.value.map((item) => [item.id, item]))
  for (const alert of alerts) {
    if (!alertIsEnabled(alert)) continue
    if (existing.has(alert.id)) {
      Object.assign(existing.get(alert.id), alert, { active: true })
      continue
    }
    const notification = { ...alert, active: true, read: false, popupClosed: false, received_at: new Date(Date.now() + clockOffsetMs).toISOString() }
    notifications.value.unshift(notification)
    existing.set(alert.id, notification)
    alertQueue.value.push(notification)
  }
  persistNotifications()
  showNextAlert()
}
function reconcileAlerts(alerts = [], items = []) {
  const monitored = new Set(items.map((item) => `${item.asset?.market}:${item.asset?.symbol}`))
  const activeIds = new Set(alerts.map((item) => item.id))
  const invalidatedAt = new Date(Date.now() + clockOffsetMs).toISOString()
  for (const item of notifications.value) {
    const key = `${item.asset?.market}:${item.asset?.symbol}`
    if (!monitored.has(key)) continue
    if (activeIds.has(item.id)) item.active = true
    else if (item.active !== false) {
      item.active = false
      item.invalidated_at = invalidatedAt
    }
  }
  persistNotifications()
}
function restoreAlertQueue() {
  alertQueue.value = notifications.value.filter((item) => item.popupClosed === false && item.active !== false && Number(item.signal_version || 0) >= 2)
  showNextAlert()
}
function closeActiveAlert() {
  if (!activeAlert.value) return
  const storedAlert = notifications.value.find((item) => item.id === activeAlert.value.id)
  if (storedAlert) storedAlert.popupClosed = true
  activeAlert.value = null
  persistNotifications()
  showNextAlert()
}
function markNotificationRead(item) {
  item.read = true
  persistNotifications()
}
function markAllRead() {
  notifications.value.forEach((item) => { item.read = true })
  persistNotifications()
}
function openNotificationAsset(item) {
  markNotificationRead(item)
  notificationOpen.value = false
  searchMarket.value = item.asset.market
  searchSymbol.value = item.asset.symbol
  loadAsset({ market: item.asset.market, symbol: item.asset.symbol, period: state.period })
}
async function refreshHealth({ quiet = true } = {}) {
  try { state.health = await fetchJson('/api/health') }
  catch (error) { if (!quiet) showToast(friendlyError(error.message)) }
}
function openWatchlistEditor() {
  draftWatchlist.value = watchlist.value.map((item) => ({ ...item, categories: [...item.categories] }))
  Object.assign(draftThresholds, thresholds)
  draftTimeZone.value = timeZoneMode.value
  draftFullForecast.value = fullForecast.value
  draftSymbol.value = ''
  watchlistOpen.value = true
  refreshHealth()
}
function toggleDraftCategory(item, category) {
  item.categories = item.categories.includes(category) ? item.categories.filter((value) => value !== category) : [...item.categories, category]
}
function addDraftAsset() {
  const symbol = draftSymbol.value.trim().toUpperCase()
  if (!/^[A-Z0-9.^=\-]{1,16}$/.test(symbol)) return showToast('请输入有效的证券、基金或商品代码')
  if (draftWatchlist.value.some((item) => item.market === draftMarket.value && item.symbol === symbol)) return showToast('该标的已在自选监测中')
  if (draftWatchlist.value.length >= 8) return showToast('自选监测最多保存 8 个标的')
  draftWatchlist.value.push({ market: draftMarket.value, symbol, categories: ['potential', 'risk', 'anomaly'] })
  draftSymbol.value = ''
}
function removeDraftAsset(index) {
  draftWatchlist.value.splice(index, 1)
}
function saveWatchlist() {
  if (!draftWatchlist.value.length) return showToast('请至少保留一个监测标的')
  if (draftWatchlist.value.some((item) => !item.categories.length)) return showToast('每个标的至少选择一种监测类别')
  const nextThresholds = { forecast: Number(draftThresholds.forecast), price: Number(draftThresholds.price), volume: Number(draftThresholds.volume) }
  if (!Number.isFinite(nextThresholds.forecast) || nextThresholds.forecast < 0.1 || nextThresholds.forecast > 20 || !Number.isFinite(nextThresholds.price) || nextThresholds.price < 0.2 || nextThresholds.price > 30 || !Number.isFinite(nextThresholds.volume) || nextThresholds.volume < 1.05 || nextThresholds.volume > 20) return showToast('监测阈值超出允许范围')
  watchlist.value = draftWatchlist.value.map((item) => ({ ...item, categories: [...item.categories] }))
  Object.assign(thresholds, nextThresholds)
  timeZoneMode.value = timeZoneOptions.some((item) => item.value === draftTimeZone.value) ? draftTimeZone.value : 'auto'
  fullForecast.value = Boolean(draftFullForecast.value)
  window.localStorage.setItem(storageKeys.watchlist, JSON.stringify(watchlist.value))
  window.localStorage.setItem(storageKeys.thresholds, JSON.stringify(thresholds))
  window.localStorage.setItem(storageKeys.timezone, JSON.stringify(timeZoneMode.value))
  window.localStorage.setItem(storageKeys.fullForecast, JSON.stringify(fullForecast.value))
  updateUtcClock()
  nextTick(renderChart)
  watchlistOpen.value = false
  showToast('设置已保存', 'success')
  loadMonitor({ quiet: false })
}

function selectSearchMarket(value) {
  searchMarket.value = value
  marketMenuOpen.value = false
}

function closeMarketMenu(event) {
  if (!event.currentTarget.contains(event.relatedTarget)) marketMenuOpen.value = false
}

async function fetchJson(url) {
  const response = await fetch(url, { headers: { Accept: 'application/json' } })
  const payload = await response.json().catch(() => null)
  if (!response.ok || !payload?.success) throw new Error(payload?.error?.message || payload?.errors?.[0]?.message || '数据请求失败')
  return payload
}

async function loadMonitor({ quiet = true } = {}) {
  if (monitorBusy || !watchlist.value.length) return
  monitorBusy = true
  state.monitoring = true
  try {
    const params = new URLSearchParams({
      assets: watchlist.value.map((item) => `${item.market}:${item.symbol}`).join(','),
      forecast: String(thresholds.forecast), price: String(thresholds.price), volume: String(thresholds.volume),
    })
    const payload = await fetchJson(`/api/monitor?${params}`)
    state.overview = payload.data.items || []
    state.streamRefresh = payload.data.refresh_seconds || 8
    state.monitorAt = new Date(payload.generated_at)
    state.lastSync = state.monitorAt
    reconcileAlerts(payload.data.alerts, payload.data.items)
    ingestAlerts(payload.data.alerts)
    if (!quiet && payload.data.failures?.length) showToast(`${payload.data.failures.length} 个标的暂时无法更新`)
  } catch (error) {
    if (!quiet) showToast(friendlyError(error.message))
  } finally {
    monitorBusy = false
    state.monitoring = false
  }
}

async function loadQuote() {
  if (quoteBusy || !state.analysis?.data?.quote) return
  const activeKey = `${state.market}:${state.symbol}`
  quoteBusy = true
  try {
    const payload = await fetchJson(`/api/quote?market=${encodeURIComponent(state.market)}&symbol=${encodeURIComponent(state.symbol)}`)
    if (`${state.market}:${state.symbol}` !== activeKey) return
    if (isFreshQuote(payload.data.quote, state.analysis.data.quote)) {
      Object.assign(state.analysis.data.asset, payload.data.asset || {})
      Object.assign(state.analysis.data.quote, payload.data.quote || {})
    }
    const current = state.overview.find((item) => item.asset.market === state.market && item.asset.symbol === state.symbol)
    if (current && isFreshQuote(payload.data.quote, current.quote)) Object.assign(current.quote, payload.data.quote || {})
    state.lastSync = new Date(payload.generated_at)
  } catch {}
  finally { quoteBusy = false }
}

function loadAssetContext(activeKey, assetType) {
  const [market, symbol] = activeKey.split(':')
  fetchJson(`/api/news?market=${encodeURIComponent(market)}&symbol=${encodeURIComponent(symbol)}`).then((news) => {
    if (`${state.market}:${state.symbol}` !== activeKey) return
    state.news = news.data
    if (news.data?.market_sentiment && state.analysis?.data) {
      state.analysis.data.market_sentiment = news.data.market_sentiment
    }
  }).catch(() => {})
  if (assetType === 'stablecoin') {
    fetchJson(`/api/stablecoin?symbol=${encodeURIComponent(symbol)}`).then((risk) => {
      if (`${state.market}:${state.symbol}` === activeKey) state.stablecoin = risk.data
    }).catch(() => {})
  }
}

async function loadAsset({ market = state.market, symbol = state.symbol, period = state.period, quiet = false } = {}) {
  if (quiet && (state.loading || state.modelLoading)) return
  const sequence = ++loadSequence
  const params = new URLSearchParams({ market, symbol, range: period.range, interval: period.interval })
  if (!quiet) {
    state.loading = true
    state.modelLoading = true
    state.stablecoin = null
    state.news = null
    try {
      const snapshot = await fetchJson(`/api/snapshot?${params}`)
      if (sequence !== loadSequence) return
      state.analysis = snapshot
      state.market = snapshot.data.asset.market
      state.symbol = snapshot.data.asset.symbol
      state.period = period
      state.lastSync = new Date(snapshot.generated_at)
      searchMarket.value = state.market
      searchSymbol.value = state.symbol
      state.loading = false
      await nextTick()
      renderChart()
    } catch {
      // The complete analysis below remains the authoritative fallback.
    }
  }
  try {
    const payload = await fetchJson(`/api/analyze?${params}`)
    if (sequence !== loadSequence) return
    const displayedQuote = quiet ? state.analysis?.data?.quote : null
    if (displayedQuote && !isFreshQuote(payload.data.quote, displayedQuote)) {
      state.lastSync = new Date(payload.generated_at)
      return
    }
    state.analysis = payload
    state.market = payload.data.asset.market
    state.symbol = payload.data.asset.symbol
    state.period = period
    state.lastSync = new Date()
    if (!quiet) {
      searchMarket.value = state.market
      searchSymbol.value = state.symbol
    }
    await nextTick()
    renderChart()
  } catch (error) {
    if (!quiet) {
      searchMarket.value = state.market
      searchSymbol.value = state.symbol
      showToast(friendlyError(error.message))
    }
  } finally {
    if (sequence === loadSequence) {
      state.loading = false
      state.modelLoading = false
      if (!quiet && state.analysis?.data?.asset) {
        loadAssetContext(`${state.market}:${state.symbol}`, state.analysis.data.asset.type)
      }
    }
  }
}

function submitSearch() {
  const value = searchSymbol.value.trim().toUpperCase()
  if (!value) return
  marketMenuOpen.value = false
  loadAsset({ market: searchMarket.value, symbol: value, period: state.period })
}

function selectOverview(item) {
  searchMarket.value = item.asset.market
  searchSymbol.value = item.asset.symbol
  loadAsset({ market: item.asset.market, symbol: item.asset.symbol, period: state.period })
}

function selectPeriod(period) {
  if (period.range === state.period.range && period.interval === state.period.interval) return
  loadAsset({ period })
}

async function renderChart() {
  if (!chartEl.value || !data.value.history?.length) return
  const echarts = await chartRuntime()
  if (!chartEl.value || !data.value.history?.length) return
  if (!chart) chart = echarts.init(chartEl.value, null, { renderer: 'canvas' })
  const chartData = buildChartData(data.value.history, forecast.value)
  backtestStats.value = chartData.backtestStats || { hits: 0, total: 0 }
  chart.setOption({
    animationDuration: 450,
    animationDurationUpdate: 320,
    backgroundColor: 'transparent',
    grid: [{ left: 12, right: 14, top: 28, height: '61%', containLabel: true }, { left: 12, right: 14, top: '74%', height: '10%', containLabel: true }],
    tooltip: { trigger: 'axis', backgroundColor: '#111513', borderColor: '#3a443b', padding: [11, 13], textStyle: { color: '#f1f4ec', fontSize: 12 }, axisPointer: { type: 'line', lineStyle: { color: '#7f8b80' } }, formatter: (params) => chartTooltip(params, chartData) },
    legend: { top: 0, right: 12, itemWidth: 20, itemHeight: 2, textStyle: { color: '#a9b2a8', fontSize: 11 }, data: ['实际价格', ...(fullForecast.value ? ['单步前瞻', '方向踏空'] : []), '未来路径', 'MA20'] },
    xAxis: [
      { type: 'category', data: chartData.axis, gridIndex: 0, boundaryGap: false, axisLine: { lineStyle: { color: '#394139' } }, axisTick: { show: false }, axisLabel: { color: '#909990', fontSize: 11, lineHeight: 15, hideOverlap: true, showMaxLabel: true, interval: 'auto', rich: { forecast: { color: '#c9baff', fontWeight: 650, lineHeight: 15 } }, formatter: (value, index) => chartAxisLabel(value, index, chartData) }, splitLine: { show: true, lineStyle: { color: '#1e2520' } } },
      { type: 'category', data: chartData.axis, gridIndex: 1, boundaryGap: false, axisLine: { lineStyle: { color: '#394139' } }, axisTick: { show: false }, axisLabel: { show: false }, splitLine: { show: false } },
    ],
    yAxis: [
      { type: 'value', scale: true, gridIndex: 0, position: 'right', axisLabel: { color: '#909990', fontSize: 11, formatter: (value) => number(value, value < 10 ? 3 : 1) }, axisLine: { show: false }, splitLine: { lineStyle: { color: '#1e2520' } } },
      { type: 'value', scale: true, gridIndex: 1, position: 'right', axisLabel: { show: false }, axisLine: { show: false }, splitLine: { show: false } },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], filterMode: 'none', zoomOnMouseWheel: true, moveOnMouseMove: true },
      { type: 'slider', xAxisIndex: [0, 1], filterMode: 'none', bottom: 0, height: 16, showDetail: false, borderColor: '#354036', backgroundColor: '#0c100d', fillerColor: 'rgba(184,255,90,.10)', dataBackground: { lineStyle: { color: '#647065', opacity: .55 }, areaStyle: { color: '#252d27', opacity: .45 } }, selectedDataBackground: { lineStyle: { color: '#b8ff5a', opacity: .8 }, areaStyle: { color: '#b8ff5a', opacity: .12 } }, handleStyle: { color: '#b8ff5a', borderColor: '#b8ff5a' }, moveHandleStyle: { color: '#778278' }, emphasis: { handleStyle: { color: '#edf1e8' }, moveHandleStyle: { color: '#b8ff5a' } } },
    ],
    series: [
      { name: '实际价格', type: 'line', data: chartData.closes, showSymbol: false, smooth: 0.12, lineStyle: { color: '#b8ff5a', width: 2.5 }, areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(184,255,90,.17)' }, { offset: 1, color: 'rgba(184,255,90,0)' }] } }, emphasis: { disabled: true }, z: 4 },
      { name: '滚动前瞻', type: 'line', data: fullForecast.value ? chartData.backtest : [], showSymbol: false, connectNulls: false, lineStyle: { color: '#8070c9', width: 1.5, type: 'dashed', opacity: .72 }, emphasis: { disabled: true }, z: 5 },
      { name: '方向踏空', type: 'scatter', data: fullForecast.value ? chartData.backtestMisses : [], symbol: 'path://M-6,-6L6,6M6,-6L-6,6', symbolSize: 9, itemStyle: { color: '#ff6b68' }, tooltip: { show: false }, emphasis: { disabled: true }, z: 8 },
      { name: '前瞻区间下界', type: 'line', data: chartData.futureLower, stack: 'forecast-interval', showSymbol: false, connectNulls: true, silent: true, lineStyle: { opacity: 0 }, areaStyle: { opacity: 0 }, emphasis: { disabled: true }, z: 1 },
      { name: '前瞻不确定区间', type: 'line', data: chartData.futureBand, stack: 'forecast-interval', showSymbol: false, connectNulls: true, silent: true, lineStyle: { opacity: 0 }, areaStyle: { color: 'rgba(178,156,255,.18)' }, emphasis: { disabled: true }, z: 1 },
      { name: '未来路径', type: 'line', data: chartData.forward, showSymbol: chartData.future.timestamps.length <= 14, symbol: 'circle', symbolSize: 6, connectNulls: true, clip: false, lineStyle: { color: '#b29cff', width: 2.6, type: 'dashed' }, itemStyle: { color: '#b29cff', borderColor: '#141915', borderWidth: 2 }, label: { show: true, position: 'top', distance: 10, color: '#d4c9ff', fontFamily: 'SFMono-Regular, Consolas, monospace', fontSize: 11, formatter: (item) => item.dataIndex === chartData.axis.length - 1 ? currency(item.value) : '' }, markArea: chartData.future.enabled ? { silent: true, itemStyle: { color: 'rgba(157,134,255,.075)' }, label: { show: true, position: 'insideTop', color: '#b8a8ed', fontSize: 10, formatter: `未来区 · ${chartData.future.horizonLabel}` }, data: [[{ xAxis: chartData.future.startKey }, { xAxis: chartData.future.endKey }]] } : undefined, emphasis: { disabled: true }, z: 7 },
      { name: 'MA20', type: 'line', data: chartData.ma20, showSymbol: false, connectNulls: false, lineStyle: { color: '#e9e7db', width: 1, opacity: .42 }, emphasis: { disabled: true }, z: 2 },
      { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: chartData.volumes, barMaxWidth: 7, itemStyle: { color: 'rgba(158,170,160,.34)' }, emphasis: { disabled: true } },
    ],
  }, true)
}

function connectStream() {
  stream?.close()
  stream = new EventSource('/api/stream')
  stream.addEventListener('connected', (event) => {
    state.live = true
    state.streamRefresh = JSON.parse(event.data).refresh_seconds || 8
  })
  stream.addEventListener('heartbeat', () => { state.live = true })
  stream.onerror = () => { state.live = false }
}

async function bootstrap() {
  updateUtcClock()
  clockTimer = window.setInterval(updateUtcClock, 1_000)
  clockSyncTimer = window.setInterval(syncClock, 300_000)
  connectStream()
  loadAsset()
  syncClock()
  window.setTimeout(() => loadMonitor({ quiet: false }), 3_500)
  window.setTimeout(refreshHealth, 5_000)
  quoteTimer = window.setInterval(loadQuote, 3_000)
  selectedTimer = window.setInterval(() => loadAsset({ quiet: true }), 10_000)
  monitorTimer = window.setInterval(() => loadMonitor(), 8_000)
}

function resizeChart() { chart?.resize() }

watch(() => [forecast.value.series, data.value.history, fullForecast.value], () => nextTick(renderChart), { deep: false })
onMounted(() => {
  restoreAlertQueue()
  bootstrap()
  window.addEventListener('resize', resizeChart)
})
onBeforeUnmount(() => {
  stream?.close()
  window.clearInterval(selectedTimer)
  window.clearInterval(quoteTimer)
  window.clearInterval(monitorTimer)
  window.clearInterval(clockTimer)
  window.clearInterval(clockSyncTimer)
  window.clearTimeout(toastTimer)
  window.removeEventListener('resize', resizeChart)
  chart?.dispose()
})
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <a class="brand" href="/" aria-label="GNITIMG Finance 首页">
        <span class="brand-mark">G</span>
        <span>GNITIMG <i>/</i> FINANCE</span>
      </a>
      <div class="top-status">
        <span class="live-status" :class="{ online: state.live }" :title="state.live ? '实时数据连接正常' : '正在重新连接'" :aria-label="state.live ? '实时数据连接正常' : '正在重新连接'"><b></b></span>
        <button type="button" class="top-icon" aria-label="消息通知" title="消息通知" @click="notificationOpen = true"><i class="ri-notification-3-line"></i><em v-if="unreadCount">{{ unreadCount > 99 ? '99+' : unreadCount }}</em></button>
        <button type="button" class="top-icon" aria-label="设置" title="设置" @click="openWatchlistEditor"><i class="ri-settings-3-line"></i></button>
        <time class="utc-clock" :class="{ synced: clockSynced }" :title="`服务器校时 · ${activeTimeZone}`">{{ marketClock }}</time>
      </div>
    </header>

    <Transition name="toast">
      <div v-if="toast.visible" class="system-toast" :class="toast.type" role="status" aria-live="polite"><i :class="toast.type === 'success' ? 'ri-check-line' : 'ri-error-warning-line'"></i><span>{{ toast.message }}</span></div>
    </Transition>

    <main>
      <section class="intro">
        <div>
          <p class="eyebrow">MARKET INTELLIGENCE</p>
          <h1>基于机器学习的市场行情监测</h1>
        </div>
        <form class="asset-search" @submit.prevent="submitSearch">
          <div class="market-select" @focusout="closeMarketMenu" @keydown.esc="marketMenuOpen = false">
            <button type="button" class="market-trigger" :aria-expanded="marketMenuOpen" aria-haspopup="listbox" @click="marketMenuOpen = !marketMenuOpen">
              <span>{{ selectedMarketOption.code }}</span><i aria-hidden="true"></i>
            </button>
            <div v-if="marketMenuOpen" class="market-menu" role="listbox" aria-label="选择市场">
              <button v-for="option in marketOptions" :key="option.value" type="button" role="option" :aria-selected="searchMarket === option.value" :class="{ active: searchMarket === option.value }" @click="selectSearchMarket(option.value)">
                <span>{{ option.code }}</span><em>{{ option.label }}</em><i>{{ searchMarket === option.value ? '✓' : '' }}</i>
              </button>
            </div>
          </div>
          <input v-model="searchSymbol" maxlength="16" placeholder="NVDA / SPY / GOLD / ES=F" aria-label="金融产品代码" />
          <button type="submit" class="search-submit">查看标的 <span>↗</span></button>
        </form>
      </section>

      <section class="ticker-strip" aria-label="自选实时监测列表">
        <div v-if="state.overview.length" class="ticker-track" :class="{ scrolling: state.overview.length >= 4 }" :style="{ '--ticker-duration': `${tickerDuration}s` }">
          <div v-for="copy in 2" :key="`ticker-copy:${copy}`" class="ticker-group" :aria-hidden="copy === 2">
            <button v-for="item in state.overview" :key="`${copy}:${item.asset.market}:${item.asset.symbol}`" :tabindex="copy === 2 ? -1 : 0" :class="{ active: item.asset.market === state.market && item.asset.symbol === state.symbol }" @click="selectOverview(item)">
              <span class="ticker-code">{{ item.asset.symbol }}</span>
              <small class="ticker-monitor" :class="monitorStatus(item).tone"><i></i>{{ monitorStatus(item).label }}</small>
              <strong>{{ number(item.quote.price, item.asset.market === 'crypto' ? 4 : 2) }}</strong>
              <em :class="Number(item.quote.change_pct || 0) >= 0 ? 'positive' : 'negative'">{{ Number(item.quote.change_pct || 0) >= 0 ? '+' : '' }}{{ number(item.quote.change_pct) }}%</em>
            </button>
          </div>
        </div>
        <span v-if="!state.overview.length" class="ticker-placeholder">正在连接市场数据…</span>
      </section>

      <section class="quote-heading" :class="{ loading: state.loading }">
        <div class="asset-identity">
          <span class="market-chip">{{ marketLabel(asset.market || state.market) }}</span>
          <div>
            <h2>{{ asset.name || state.symbol }}</h2>
            <p>{{ asset.symbol || state.symbol }} · {{ asset.exchange || 'MARKET' }}</p>
          </div>
        </div>
        <div class="headline-price">
          <strong>{{ currency(quote.price) }}</strong>
          <span :class="changeClass">{{ Number(quote.change || 0) >= 0 ? '+' : '' }}{{ number(quote.change) }} / {{ Number(quote.change_pct || 0) >= 0 ? '+' : '' }}{{ number(quote.change_pct) }}%</span>
          <small><span v-if="quote.realtime === false" class="delay-flag">延迟行情</span><template v-if="quote.realtime === false"> · </template><span v-if="openChangePct !== null" :class="openChangePct >= 0 ? 'positive' : 'negative'">较开盘 {{ openChangePct >= 0 ? '+' : '' }}{{ number(openChangePct) }}%</span><template v-if="openChangePct !== null"> · </template>{{ quote.market_state === 'REGULAR' || quote.market_state === 'OPEN_24_7' ? '交易中' : '已收盘' }} · {{ dateTime(quote.as_of) }}</small>
        </div>
      </section>

      <section class="dashboard-grid">
        <article class="panel chart-panel">
          <div class="panel-head">
            <div><span class="section-index">01</span><h3>价格轨迹</h3></div>
            <div class="periods">
              <button v-for="period in periods" :key="period.label" :class="{ active: period.label === state.period.label }" @click="selectPeriod(period)">{{ period.label }}</button>
            </div>
          </div>
          <div ref="chartEl" class="price-chart" :class="{ muted: state.loading }"></div>
          <div class="chart-key">
            <div class="key-items">
              <span><i class="actual-line"></i>实际价格</span>
              <span v-if="fullForecast"><i class="backtest-line"></i>单步前瞻<template v-if="backtestStats.total"> · 命中 {{ backtestStats.hits }}/{{ backtestStats.total }}</template></span>
              <span><i class="forward-line"></i>未来路径</span>
              <span><i class="range-box"></i>模型区间</span>
            </div>
            <div v-if="forecast.next_forecast" class="forward-readout">
              <span>{{ forecast.horizon_label }}</span>
              <strong>{{ currency(forecast.next_forecast.predicted_price) }}</strong>
              <em :class="Number(forecast.next_forecast.predicted_return_pct || 0) >= 0 ? 'positive' : 'negative'">{{ Number(forecast.next_forecast.predicted_return_pct || 0) >= 0 ? '+' : '' }}{{ number(forecast.next_forecast.predicted_return_pct) }}%</em>
              <small v-if="forecast.next_forecast.lower_price != null">区间 {{ currency(forecast.next_forecast.lower_price) }} – {{ currency(forecast.next_forecast.upper_price) }}</small>
            </div>
            <p v-else>模型正在积累足够样本；实际结果到期后会自动回灌。</p>
          </div>
        </article>

        <aside class="panel signal-panel">
          <div class="panel-head"><div><span class="section-index">02</span><h3>信号台</h3></div><span class="stance" :class="technical.stance">{{ stanceLabel(technical.stance) }}</span></div>
          <div class="signal-score"><span>综合信号</span><strong>{{ technical.score ?? '—' }}</strong><div><i :style="{ width: `${Math.abs(technical.score || 0)}%`, marginLeft: Number(technical.score || 0) >= 0 ? '50%' : `${50 - Math.abs(technical.score || 0) / 2}%` }"></i></div></div>
          <ul class="signal-list">
            <li v-for="signal in technical.signals?.slice(0, 5)" :key="signal.key">
              <i :class="signal.direction"></i><span>{{ signalLabel(signal.label) }}</span><em>{{ signal.direction === 'bullish' ? '强' : signal.direction === 'bearish' ? '弱' : '中性' }}</em>
            </li>
            <li v-if="!technical.signals?.length" class="empty-state">等待足够历史数据</li>
          </ul>
          <div class="source-note"><span>{{ quote.source || '—' }}</span><p>{{ quote.feed || '等待数据源' }}</p><em :class="data.cache?.stale ? 'warn' : ''">{{ data.cache?.stale ? 'STALE' : data.cache?.cached ? 'CACHED' : 'FRESH' }}</em></div>
        </aside>

        <article class="panel metric-panel">
          <div class="panel-head"><div><span class="section-index">03</span><h3>市场剖面</h3></div></div>
          <div class="metrics">
            <div><span>开盘价</span><strong>{{ number(quote.open, 3) }}</strong></div>
            <div><span>日内高点</span><strong>{{ number(quote.high, 3) }}</strong></div>
            <div><span>日内低点</span><strong>{{ number(quote.low, 3) }}</strong></div>
            <div><span>昨收</span><strong>{{ number(quote.previous_close, 3) }}</strong></div>
            <div><span>较开盘</span><strong :class="openChangePct === null ? '' : openChangePct >= 0 ? 'positive' : 'negative'">{{ openChangePct === null ? '—' : `${openChangePct >= 0 ? '+' : ''}${number(openChangePct)}%` }}</strong></div>
            <div><span>成交量</span><strong>{{ compact(quote.volume) }}</strong></div>
            <div><span>RSI 14</span><strong>{{ number(technical.rsi14, 1) }}</strong></div>
            <div><span>ATR</span><strong>{{ number(technical.atr_pct, 2) }}<small>%</small></strong></div>
            <div><span>年化波动</span><strong>{{ number(technical.annualized_volatility_pct, 1) }}<small>%</small></strong></div>
          </div>
          <div class="levels">
            <div v-for="level in technical.levels?.slice(0, 4)" :key="`${level.label}:${level.value}`">
              <span>{{ levelLabel(level.label) }}</span><i :class="level.kind"></i><strong>{{ number(level.value, 3) }}</strong><em>{{ level.kind === 'support' ? '支撑' : '阻力' }}</em>
            </div>
          </div>
        </article>

        <article class="panel model-panel">
          <div class="panel-head"><div><span class="section-index">04</span><h3>前瞻模型</h3></div><span class="python-tag">{{ state.modelLoading ? 'PYTHON / 正在更新' : `PYTHON / ${profileLabel(forecast.ensemble?.profile)}` }}</span></div>
          <div v-if="forecast.next_forecast" class="model-forecast">
            <div><span>预测窗口 · {{ forecast.horizon_label }} · {{ forecastStateLabel() }}</span><strong>{{ currency(forecast.next_forecast.predicted_price) }}</strong><em :class="Number(forecast.next_forecast.predicted_return_pct || 0) >= 0 ? 'positive' : 'negative'">{{ Number(forecast.next_forecast.predicted_return_pct || 0) >= 0 ? '+' : '' }}{{ number(forecast.next_forecast.predicted_return_pct) }}%</em></div>
            <div class="confidence-ring" :title="forecast.confidence?.capped_by_validation ? '未通过样本外验证，分数封顶在 34' : '历史校准质量'" :style="{ '--confidence': `${forecast.confidence?.score || 0}%` }"><strong>{{ number(forecast.confidence?.score, 0) }}</strong><span>/100</span><em v-if="forecast.confidence?.capped_by_validation">封顶</em></div>
          </div>
          <div v-else-if="state.modelLoading" class="model-unavailable"><span>模型计算中</span><strong>行情已就绪，正在更新前瞻</strong><p>页面无需等待完整回测即可先查看报价与价格轨迹。</p></div>
          <div v-else class="model-unavailable"><span>样本积累中</span><strong>当前数据不足以形成可靠前瞻</strong><p>系统会继续接收实际结果，达到最低校准样本后自动启用。</p></div>
          <div class="model-stats">
            <div><span>校准结论</span><strong :class="forecast.evaluation?.validation_passed === false ? 'negative' : ''">{{ forecast.evaluation?.validation_passed === false ? validationLabel(false) : gradeLabel(forecast.confidence?.grade) }}</strong></div>
            <div><span>方向命中</span><strong>{{ number(forecast.evaluation?.directional_accuracy, 1) }}%</strong></div>
            <div><span>平均误差</span><strong>{{ number(forecast.evaluation?.mean_absolute_error_pct, 2) }}%</strong></div>
            <div><span>实际回灌</span><strong>{{ forecast.training?.online_updates ?? '—' }} 次</strong></div>
            <div><span>回测相位</span><strong>{{ phaseLabel(forecast.evaluation?.phase_lag_bars) }}</strong></div>
            <div><span>相对静止基线</span><strong :class="Number(forecast.evaluation?.skill_vs_no_change_pct || 0) >= 0 ? 'positive' : 'negative'">{{ Number(forecast.evaluation?.skill_vs_no_change_pct || 0) >= 0 ? '+' : '' }}{{ number(forecast.evaluation?.skill_vs_no_change_pct, 1) }}%</strong></div>
            <div><span>实时情绪</span><strong :class="marketSentiment.label">{{ Number(marketSentiment.score || 0) >= 0 ? '+' : '' }}{{ number(marketSentiment.score, 0) }}</strong></div>
            <div><span>训练隔离</span><strong>单标的状态</strong></div>
          </div>
          <p class="model-disclaimer">{{ fullForecast ? '深紫虚线为单步滚动回测——每一步只用该时点之前的数据（5 分钟视图约为此前 105 分钟，日线约为此前 21 个交易日）预测下一根 bar，红叉为方向踏空处；' : '' }}亮紫路径综合价格、量能、技术结构与关联内容逐步向前计算，半透明区域来自留出样本误差。反转闸门会撤销与最新状态冲突的方向。置信度不是涨跌概率。</p>
        </article>
      </section>

      <section class="lower-grid">
        <article class="panel watch-panel">
          <div class="panel-head"><div><span class="section-index">05</span><h3>实时观察</h3></div><span class="refresh-note">报价 3 秒 · 模型 10 秒 · 监测 {{ state.streamRefresh }} 秒</span></div>
          <div class="watch-table">
            <div class="watch-row table-head"><span>标的</span><span>市场</span><span>价格</span><span>变动</span><span>状态</span></div>
            <button v-for="item in state.overview" :key="`row:${item.asset.market}:${item.asset.symbol}`" class="watch-row" @click="selectOverview(item)">
              <span><strong>{{ item.asset.symbol }}</strong><small>{{ item.asset.name }}</small></span>
              <span>{{ marketLabel(item.asset.market) }}</span>
              <span>{{ number(item.quote.price, item.asset.market === 'crypto' ? 4 : 2) }}</span>
              <span :class="Number(item.quote.change_pct || 0) >= 0 ? 'positive' : 'negative'">{{ Number(item.quote.change_pct || 0) >= 0 ? '+' : '' }}{{ number(item.quote.change_pct) }}%</span>
              <span class="watch-status" :class="monitorStatus(item).tone"><i></i>{{ monitorStatus(item).label }}</span>
            </button>
          </div>
        </article>

        <aside class="panel system-panel">
          <div class="panel-head"><div><span class="section-index">06</span><h3>数据链路</h3></div></div>
          <div class="provider-list">
            <div v-for="([name, provider]) in providerSummary" :key="name"><i :class="{ ok: provider.ok }"></i><span>{{ name }}</span><strong>{{ provider.ok ? 'ONLINE' : 'DEGRADED' }}</strong><em>{{ number(provider.latency_ms, 0) }} ms</em></div>
          </div>
          <div class="engine-rule"><span>执行原则</span><p>报价、指标、监测、模型训练全程 Python。只有明确的深度综合请求才允许进入可选 L2 推理。</p></div>
        </aside>
      </section>

      <section class="panel news-panel">
        <div class="panel-head">
          <div><span class="section-index">07</span><h3>市场情绪与关联内容</h3></div>
          <div v-if="marketSentiment.regime" class="sentiment-badge" :class="marketSentiment.label"><span>{{ sentimentLabel(marketSentiment.label) }}</span><strong>{{ marketSentiment.regime }} · 证据覆盖 {{ number(marketSentiment.confidence, 0) }}/100</strong></div>
        </div>
        <div v-if="marketSentiment.factors?.length" class="sentiment-console">
          <div class="sentiment-meter">
            <span>综合市场情绪</span>
            <strong :class="marketSentiment.label">{{ Number(marketSentiment.score || 0) >= 0 ? '+' : '' }}{{ number(marketSentiment.score, 0) }}</strong>
            <em>异动强度 {{ number(marketSentiment.abnormal?.score, 0) }}/100</em>
            <div><i :style="{ width: `${Math.abs(marketSentiment.score || 0) / 2}%`, marginLeft: Number(marketSentiment.score || 0) >= 0 ? '50%' : `${50 - Math.abs(marketSentiment.score || 0) / 2}%` }"></i></div>
          </div>
          <div class="sentiment-factors">
            <div v-for="factor in marketSentiment.factors" :key="factor.key" class="sentiment-factor">
              <span><i :class="factor.direction"></i>{{ factor.label }}</span>
              <strong>{{ factor.display }}</strong>
              <em>{{ factor.source || '当前源未覆盖' }}</em>
            </div>
          </div>
        </div>
        <div v-if="state.news?.items?.length" class="news-list">
          <a v-for="item in state.news.items.slice(0, 6)" :key="item.url || item.title" :href="safeUrl(item.url)" target="_blank" rel="noopener noreferrer">
            <span>{{ item.source }}</span><h4>{{ item.title }}</h4><em>{{ item.published_at || '时间未提供' }} ↗</em>
          </a>
        </div>
        <div v-else class="news-empty">当前数据源没有返回可验证消息。行情与技术分析不受影响。</div>
        <p class="news-footnote">价格异动、量能、振幅、技术结构与关联内容由 Python 确定性合成；不调用 LLM，也不代表未来收益。</p>
      </section>

      <section v-if="state.stablecoin" class="stablecoin-band">
        <span>稳定币监测</span><strong>{{ state.stablecoin.risk.level.toUpperCase() }} / {{ number(state.stablecoin.risk.score, 1) }}</strong><p>偏离锚定 {{ number(state.stablecoin.risk.depeg_pct, 4) }}% · {{ state.stablecoin.coverage?.level === 'full' ? '价格、DEX 流动性与供应量均已覆盖。' : '当前为部分覆盖，未取得的链上指标不计入评分。' }} 风险分不是违约概率。</p>
      </section>
    </main>

    <footer>
      <div><strong>GNITIMG FINANCE</strong><p>Verified data. Deterministic first.</p></div>
      <p>公开行情可能存在交易所延迟。统计预测不构成投资建议、收益承诺或交易指令。</p>
      <span>© 2026 GNITIMG</span>
    </footer>

    <div v-if="watchlistOpen" class="modal-layer" role="presentation">
      <section class="watchlist-dialog settings-dialog" role="dialog" aria-modal="true" aria-labelledby="watchlist-title">
        <header><div><span>FINANCE SETTINGS</span><h2 id="watchlist-title">设置</h2></div><button type="button" aria-label="关闭设置" @click="watchlistOpen = false"><i class="ri-close-line"></i></button></header>
        <div class="settings-body">
        <nav class="settings-nav" aria-label="设置目录">
          <button v-for="entry in settingsSections" :key="entry.id" type="button" :class="{ active: settingsSection === entry.id }" @click="settingsSection = entry.id"><span>{{ entry.index }}</span><strong>{{ entry.label }}</strong></button>
        </nav>
        <div class="settings-content">
        <section v-show="settingsSection === 'timezone'" class="settings-block timezone-settings">
          <div class="settings-title"><div><span>01</span><h3>显示时区</h3></div></div>
          <div class="timezone-options">
            <button v-for="option in timeZoneOptions" :key="option.value" type="button" :class="{ active: draftTimeZone === option.value }" @click="draftTimeZone = option.value"><i :class="draftTimeZone === option.value ? 'ri-radio-button-line' : 'ri-checkbox-blank-circle-line'"></i><span>{{ option.label }}</span><em>{{ option.detail }}</em></button>
          </div>
        </section>
        <section v-show="settingsSection === 'forecast'" class="settings-block forecast-settings">
          <div class="settings-title"><div><span>02</span><h3>预测轨迹</h3></div></div>
          <label class="setting-switch">
            <div><strong>显示完整预测轨迹</strong><span>在主图加入单次锚定、连续推进的历史滚动前瞻</span></div>
            <input v-model="draftFullForecast" type="checkbox" />
            <i aria-hidden="true"></i>
          </label>
        </section>
        <section v-show="settingsSection === 'watchlist'" class="settings-block watchlist-settings">
          <div class="settings-title"><div><span>03</span><h3>自选与预警</h3></div></div>
        <div class="watch-add">
          <div class="watch-market-pills" aria-label="新增标的市场">
            <button v-for="option in marketOptions.filter((item) => item.value !== 'auto')" :key="`draft:${option.value}`" type="button" :class="{ active: draftMarket === option.value }" @click="draftMarket = option.value">{{ option.label }}</button>
          </div>
          <form @submit.prevent="addDraftAsset"><input v-model="draftSymbol" maxlength="16" placeholder="如 SPY / VTSAX / GOLD / ES=F" aria-label="新增自选代码" /><button type="submit"><i class="ri-add-line"></i>添加</button></form>
        </div>
        <div class="watch-editor-list">
          <div v-for="(item, index) in draftWatchlist" :key="`${item.market}:${item.symbol}`" class="watch-editor-row">
            <div class="watch-editor-asset"><span>{{ marketLabel(item.market) }}</span><strong>{{ item.symbol }}</strong></div>
            <div class="category-toggles">
              <button v-for="category in monitorCategories" :key="category.value" type="button" :class="[category.value, { active: item.categories.includes(category.value) }]" :title="category.description" @click="toggleDraftCategory(item, category.value)">{{ category.label }}</button>
            </div>
            <button type="button" class="remove-watch" aria-label="移除该标的" @click="removeDraftAsset(index)"><i class="ri-delete-bin-line"></i></button>
          </div>
        </div>
        <div class="threshold-editor">
          <label><span>前瞻涨跌触发</span><div><input v-model.number="draftThresholds.forecast" type="number" min="0.1" max="20" step="0.1" /><em>%</em></div></label>
          <label><span>日内涨跌异动</span><div><input v-model.number="draftThresholds.price" type="number" min="0.2" max="30" step="0.1" /><em>%</em></div></label>
          <label><span>相对量能异动</span><div><input v-model.number="draftThresholds.volume" type="number" min="1.05" max="20" step="0.05" /><em>×</em></div></label>
        </div>
        </section>
        <section v-show="settingsSection === 'health'" class="settings-block health-settings">
          <div class="settings-title"><div><span>04</span><h3>数据源健康</h3></div><button type="button" class="health-refresh" @click="refreshHealth({ quiet: false })"><i class="ri-refresh-line"></i>重新检测</button></div>
          <div class="settings-health">
            <div v-for="([name, provider]) in providerSummary" :key="`setting:${name}`"><i :class="{ ok: provider.ok }"></i><span>{{ name }}</span><strong>{{ provider.ok ? '正常' : '降级' }}</strong><em>{{ number(provider.latency_ms, 0) }} ms</em></div>
            <p v-if="!providerSummary.length">正在检测数据源…</p>
          </div>
          <p class="health-note">行情源失败时只会在允许的新鲜度窗口内使用缓存，并明确标记；不会用模型补造实时价格。</p>
        </section>
        <section v-show="settingsSection === 'about'" class="settings-block about-settings">
          <div class="settings-title"><div><span>05</span><h3>关于</h3></div></div>
          <div class="about-list">
            <div><span>开源仓库</span><p>本站为 GNITIMG Finance。确定性 Python 引擎、Node 服务端与前端已开源：<a href="https://github.com/gnitimg/Finance" target="_blank" rel="noopener noreferrer">github.com/gnitimg/Finance</a>。</p></div>
            <div><span>信息保护</span><p>行情获取、指标计算、模型训练与告警全部在本站服务器本地完成；不注册、不收集账号、位置或浏览历史。自选列表、时区与已读状态等偏好仅保存在你的浏览器本地。可选的 L2 语言模型综合默认关闭；如启用，仅传输脱敏后的紧凑市场摘要，发送前自动过滤密钥、手机号、账号与频道标识。</p></div>
            <div><span>法规遵从</span><p>依据《中华人民共和国个人信息保护法》《数据安全法》《网络安全法》按最小必要原则处理数据；对欧盟等地区访客参照 GDPR 的透明处理与目的限定要求执行；若未来启用生成式 AI 输出，将遵守《生成式人工智能服务管理暂行办法》并对合成内容作出标识。</p></div>
            <div><span>算法说明</span><p>报价、技术指标、风险与告警全部由确定性 Python 引擎计算，不调用大语言模型。前瞻由多分量集成模型生成：正则回归、历史相似形态、短期动量先验与实时市场语境，经时间顺序留出集验证；置信度表示历史校准质量而非涨跌概率，未通过验证时预测自动降幅并封顶显示。</p></div>
            <div><span>数据来源</span><p>A 股快照来自新浪财经，全球 K 线来自 Yahoo Finance（可能存在交易所延迟），加密资产来自 CoinGecko，资金流与盘口来自公开接口，新闻来自 GDELT 与 Yahoo Finance。每个响应都携带数据来源、时间戳与延迟标记。</p></div>
            <div><span>免责条款</span><p>本站全部内容仅供信息参考与研究用途，不构成投资建议、收益承诺或交易指令。行情可能延迟，统计预测存在误差，历史表现不代表未来。据此操作，风险自负。</p></div>
          </div>
        </section>
        </div>
        </div>
        <footer><span>最多 8 个标的 · 时区 {{ draftTimeZone === 'auto' ? browserTimeZone : draftTimeZone }}</span><button type="button" @click="saveWatchlist">保存设置</button></footer>
      </section>
    </div>

    <div v-if="notificationOpen" class="drawer-layer" role="presentation">
      <aside class="notification-drawer" role="dialog" aria-modal="true" aria-labelledby="notification-title">
        <header><div><span>ALERT ARCHIVE</span><h2 id="notification-title">消息通知</h2></div><button type="button" aria-label="关闭消息通知" @click="notificationOpen = false"><i class="ri-close-line"></i></button></header>
        <div class="notification-tools"><span>{{ unreadCount }} 条未读</span><button type="button" @click="markAllRead">全部标为已读</button></div>
        <div v-if="notifications.length" class="notification-list">
          <button v-for="item in notifications" :key="item.id" type="button" :class="[item.category, { unread: !item.read, expired: item.active === false }]" @click="openNotificationAsset(item)">
            <i></i><div><span>{{ item.active === false ? '已失效 · ' : '' }}{{ categoryLabel(item.category) }} · {{ item.asset.symbol }}</span><strong>{{ item.title }}</strong><p>{{ item.summary }}</p><em>{{ dateTime(item.received_at || item.as_of) }} · {{ item.source }}<template v-if="item.active === false && item.invalidated_at"> · {{ dateTime(item.invalidated_at) }} 失效</template></em></div><b class="ri-arrow-right-up-line"></b>
          </button>
        </div>
        <div v-else class="notification-empty"><i class="ri-notification-off-line"></i><strong>暂无监测消息</strong><p>达到自选阈值的潜力、风险或异动会保留在这里。</p></div>
      </aside>
    </div>

    <div v-if="activeAlert" class="alert-layer" role="presentation">
      <article class="market-alert" :class="activeAlert.category" role="alertdialog" aria-modal="true" aria-labelledby="market-alert-title">
        <div class="alert-kicker"><span>{{ categoryLabel(activeAlert.category) }} SIGNAL</span><em>{{ activeAlert.severity.toUpperCase() }}</em></div>
        <h2 id="market-alert-title">{{ activeAlert.title }}</h2>
        <div v-if="activeAlert.active === false" class="alert-invalidated">当前信号已失效，系统已停止继续触发</div>
        <p>{{ activeAlert.summary }}</p>
        <ul><li v-for="evidence in activeAlert.evidence" :key="evidence"><i></i>{{ evidence }}</li></ul>
        <div class="alert-source"><span>{{ activeAlert.source }}</span><em>{{ dateTime(activeAlert.as_of) }}<template v-if="activeAlert.target_time"> · 验证时间 {{ dateTime(activeAlert.target_time) }}</template></em></div>
        <button type="button" class="alert-close" @click="closeActiveAlert">收下并关闭</button>
      </article>
    </div>
  </div>
</template>
