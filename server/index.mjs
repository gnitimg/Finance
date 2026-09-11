import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import express from 'express'
import compression from 'compression'
import helmet from 'helmet'
import { rateLimit } from 'express-rate-limit'
import { compactOverviewData, parseAssetList, publicError, runFinance, validateAsset, validateMonitorThresholds, validatePeriod } from './lib.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const DIST = path.join(ROOT, 'web', 'dist')

function loadEnv() {
  const file = path.join(ROOT, '.env')
  if (!fs.existsSync(file)) return
  for (const raw of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    const line = raw.trim()
    if (!line || line.startsWith('#') || !line.includes('=')) continue
    const index = line.indexOf('=')
    const key = line.slice(0, index).trim()
    const value = line.slice(index + 1).trim().replace(/^['"]|['"]$/g, '')
    if (!(key in process.env)) process.env[key] = value
  }
}
loadEnv()

const PORT = Number(process.env.PORT || 8790)
const REFRESH_SECONDS = Math.max(3, Math.min(Number(process.env.FINANCE_REFRESH_SECONDS || 5), 120))
const DEFAULT_ASSETS = parseAssetList(process.env.FINANCE_DEFAULT_ASSETS || 'us:NVDA,us:AAPL,hk:00700,cn:601619,crypto:USDT,etf:SPY,metal:GOLD')
const app = express()
app.set('trust proxy', 'loopback')
app.disable('x-powered-by')
app.use(helmet({
  contentSecurityPolicy: { directives: { defaultSrc: ["'self'"], scriptSrc: ["'self'"], styleSrc: ["'self'", "'unsafe-inline'"], imgSrc: ["'self'", 'data:'], connectSrc: ["'self'"], fontSrc: ["'self'", 'data:'], objectSrc: ["'none'"], frameAncestors: ["'none'"] } },
  crossOriginEmbedderPolicy: false,
  strictTransportSecurity: false,
}))
app.use(compression({ filter: (req, res) => req.path === '/api/stream' ? false : compression.filter(req, res) }))
app.use(express.json({ limit: '20kb' }))
app.use('/api', rateLimit({ windowMs: 60_000, limit: 240, standardHeaders: 'draft-8', legacyHeaders: false }))

const memory = new Map()
const pending = new Map()
async function cached(key, ttlMs, task) {
  const entry = memory.get(key)
  if (entry && Date.now() - entry.at < ttlMs) return entry.value
  if (pending.has(key)) return pending.get(key)
  const promise = task().then((value) => {
    memory.set(key, { value, at: Date.now() })
    pending.delete(key)
    return value
  }).catch((error) => { pending.delete(key); throw error })
  pending.set(key, promise)
  return promise
}

app.get('/api/quote', async (req, res) => {
  try {
    const { market, symbol } = validateAsset(req.query.market, req.query.symbol)
    const result = await cached(`quote:${market}:${symbol}`, 2000, () => runFinance(['quote', '--market', market, '--symbol', symbol]))
    res.json(result)
  } catch (error) { res.status(error.statusCode || 400).json(publicError(error)) }
})

app.get('/api/analyze', async (req, res) => {
  try {
    const { market, symbol } = validateAsset(req.query.market, req.query.symbol)
    const period = validatePeriod(req.query.range, req.query.interval)
    const key = `analyze:${market}:${symbol}:${period.range}:${period.interval}`
    const result = await cached(key, period.interval === '1d' ? 60_000 : 8_000, () => runFinance(['analyze', '--market', market, '--symbol', symbol, '--range', period.range, '--interval', period.interval], { timeoutMs: 25_000 }))
    res.json(result)
  } catch (error) { res.status(error.statusCode || 400).json(publicError(error)) }
})

app.get('/api/stablecoin', async (req, res) => {
  try {
    const { symbol } = validateAsset('crypto', req.query.symbol)
    const result = await cached(`stablecoin:${symbol}`, 30_000, () => runFinance(['stablecoin', '--symbol', symbol], { timeoutMs: 20_000 }))
    res.json(result)
  } catch (error) { res.status(error.statusCode || 400).json(publicError(error)) }
})

app.get('/api/news', async (req, res) => {
  try {
    const { market, symbol } = validateAsset(req.query.market, req.query.symbol)
    const result = await cached(`news:${market}:${symbol}`, 45_000, () => runFinance(['news', '--market', market, '--symbol', symbol, '--limit', '10'], { timeoutMs: 25_000 }))
    res.json(result)
  } catch (error) { res.status(error.statusCode || 400).json(publicError(error)) }
})

app.get('/api/time', (_req, res) => {
  const now = new Date()
  res.json({ success: true, utc: now.toISOString(), epoch_ms: now.getTime() })
})

app.get('/api/monitor', async (req, res) => {
  try {
    const assets = parseAssetList(req.query.assets)
    if (!assets.length) throw new Error('monitor requires at least one asset')
    const thresholds = validateMonitorThresholds(req.query)
    const args = ['monitor']
    for (const asset of assets) args.push('--asset', asset)
    args.push('--forecast-pct', String(thresholds.forecast_pct), '--price-change-pct', String(thresholds.price_change_pct), '--volume-ratio', String(thresholds.volume_ratio))
    const key = `monitor:${assets.join(',')}:${thresholds.forecast_pct}:${thresholds.price_change_pct}:${thresholds.volume_ratio}`
    const result = await cached(key, 8_000, () => runFinance(args, { timeoutMs: 45_000 }))
    res.json(result)
  } catch (error) { res.status(error.statusCode || 400).json(publicError(error)) }
})

app.get('/api/health', async (_req, res) => {
  try { res.json(await cached('health', 60_000, () => runFinance(['health'], { timeoutMs: 20_000 }))) }
  catch (error) { res.status(error.statusCode || 503).json(publicError(error)) }
})

async function loadOverview(assets = DEFAULT_ASSETS) {
  const settled = await Promise.allSettled(assets.map(async (asset) => {
    const [market, symbol] = asset.split(':')
    const result = await cached(`quote:${market}:${symbol}`, 2000, () => runFinance(['quote', '--market', market, '--symbol', symbol]))
    return compactOverviewData(result.data)
  }))
  return {
    success: settled.some((item) => item.status === 'fulfilled'),
    generated_at: new Date().toISOString(),
    refresh_seconds: REFRESH_SECONDS,
    assets: settled.filter((item) => item.status === 'fulfilled').map((item) => item.value),
    failures: settled.filter((item) => item.status === 'rejected').length,
  }
}

app.get('/api/overview', async (req, res) => {
  try {
    const assets = req.query.assets ? parseAssetList(req.query.assets) : DEFAULT_ASSETS
    res.json(await loadOverview(assets))
  } catch (error) { res.status(400).json(publicError(error)) }
})

const clients = new Set()
function broadcast(event, payload) {
  const frame = `event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`
  for (const response of clients) response.write(frame)
}
app.get('/api/stream', (req, res) => {
  res.set({ 'Content-Type': 'text/event-stream; charset=utf-8', 'Cache-Control': 'no-cache, no-transform', Connection: 'keep-alive', 'X-Accel-Buffering': 'no' })
  res.flushHeaders()
  clients.add(res)
  res.write(`event: connected\ndata: ${JSON.stringify({ refresh_seconds: REFRESH_SECONDS })}\n\n`)
  req.on('close', () => clients.delete(res))
})

setInterval(() => broadcast('heartbeat', { at: new Date().toISOString() }), 10_000).unref()

app.use(express.static(DIST, { index: false, maxAge: '1h', immutable: false }))
app.get('*', (_req, res) => res.sendFile(path.join(DIST, 'index.html')))
app.use((error, _req, res, _next) => res.status(500).json(publicError(error)))

app.listen(PORT, '127.0.0.1', () => {
  console.log(`gnitimg-finance listening on http://127.0.0.1:${PORT}`)
})
