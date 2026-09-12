import crypto from 'node:crypto'
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const VENV_PYTHON = path.join(ROOT, '.venv', 'bin', 'python')
const PYTHON = process.env.FINANCE_PYTHON || (fs.existsSync(VENV_PYTHON) ? VENV_PYTHON : 'python3')
const SCRIPT = path.join(ROOT, 'scripts', 'finance.py')
const MARKETS = new Set(['auto', 'cn', 'hk', 'us', 'crypto', 'etf', 'fund', 'future', 'metal'])
export const PERIODS = new Set(['1d:5m', '5d:15m', '5d:30m', '1mo:5m', '1mo:1d', '3mo:1d', '6mo:1d', '1y:1d', '2y:1d', '5y:1d'])

export function validateAsset(market, symbol) {
  const normalizedMarket = String(market || 'auto').toLowerCase()
  const normalizedSymbol = String(symbol || '').trim().toUpperCase()
  if (!MARKETS.has(normalizedMarket)) throw new Error('invalid market')
  if (!/^[A-Z0-9.^=-]{1,16}$/.test(normalizedSymbol)) throw new Error('invalid symbol')
  return { market: normalizedMarket, symbol: normalizedSymbol }
}

export function validatePeriod(rangeName, interval) {
  const range = String(rangeName || '3mo')
  const frame = String(interval || '1d')
  if (!PERIODS.has(`${range}:${frame}`)) throw new Error('invalid range/interval')
  return { range, interval: frame }
}

export function parseAssetList(value) {
  return String(value || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 8)
    .map((item) => {
      const [market, symbol] = item.includes(':') ? item.split(':', 2) : ['auto', item]
      const normalized = validateAsset(market, symbol)
      return `${normalized.market}:${normalized.symbol}`
    })
}

export function validateMonitorThresholds(values = {}) {
  const parse = (value, fallback, minimum, maximum) => {
    const number = value === undefined || value === '' ? fallback : Number(value)
    if (!Number.isFinite(number) || number < minimum || number > maximum) throw new Error('invalid monitor threshold')
    return number
  }
  return {
    forecast_pct: parse(values.forecast, 0.7, 0.1, 20),
    price_change_pct: parse(values.price, 2.0, 0.2, 30),
    volume_ratio: parse(values.volume, 1.8, 1.05, 20),
  }
}

export function compactOverviewData(data) {
  return {
    asset: data.asset,
    quote: data.quote,
    cache: data.cache,
    provider_timing: data.provider_timing,
  }
}

export function runFinance(args, { timeoutMs = 20000 } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, [SCRIPT, ...args], {
      cwd: ROOT,
      // Web requests render the primary feed first and consume only pre-warmed
      // East Money context. Direct finance-skill CLI use may warm its own cache.
      env: { ...process.env, PYTHONUNBUFFERED: '1', FINANCE_FAST_PATH: '1' },
      stdio: ['ignore', 'pipe', 'pipe'],
      shell: false,
    })
    let stdout = ''
    let stderr = ''
    const timer = setTimeout(() => {
      child.kill('SIGKILL')
      reject(Object.assign(new Error('finance engine timed out'), { statusCode: 504 }))
    }, timeoutMs)
    child.stdout.on('data', (chunk) => {
      stdout += chunk
      if (stdout.length > 3_000_000) child.kill('SIGKILL')
    })
    child.stderr.on('data', (chunk) => { stderr += chunk })
    child.on('error', (error) => {
      clearTimeout(timer)
      reject(error)
    })
    child.on('close', () => {
      clearTimeout(timer)
      try {
        const payload = JSON.parse(stdout)
        if (!payload.success) {
          const error = new Error(payload.errors?.[0]?.message || 'finance engine failed')
          error.statusCode = 422
          error.payload = payload
          reject(error)
          return
        }
        resolve(payload)
      } catch {
        const error = new Error(stderr.trim() || 'finance engine returned invalid JSON')
        error.statusCode = 502
        reject(error)
      }
    })
  })
}

export function publicError(error) {
  return {
    success: false,
    error: {
      code: error.statusCode === 504 ? 'ENGINE_TIMEOUT' : error.statusCode === 422 ? 'UPSTREAM_UNAVAILABLE' : 'REQUEST_FAILED',
      message: error.message || 'request failed',
    },
  }
}

const MODEL_CONFIG_PATH = path.join(ROOT, 'data', 'model_endpoints.json')
const MODEL_KINDS = new Set(['chat', 'rerank', 'embedding'])

function readModelConfig() {
  try {
    const payload = JSON.parse(fs.readFileSync(MODEL_CONFIG_PATH, 'utf8'))
    if (payload && Array.isArray(payload.models)) return payload
  } catch {}
  return { models: [] }
}

function writeModelConfig(payload) {
  fs.mkdirSync(path.dirname(MODEL_CONFIG_PATH), { recursive: true })
  fs.writeFileSync(MODEL_CONFIG_PATH, JSON.stringify(payload, null, 2), 'utf8')
}

function maskKey(key) {
  if (!key) return ''
  return key.length > 9 ? `${key.slice(0, 4)}****${key.slice(-3)}` : '****'
}

export function listModelConfigs() {
  return readModelConfig().models.map((entry) => ({
    id: entry.id, name: entry.name, kind: entry.kind, base_url: entry.base_url,
    model: entry.model, enabled: Boolean(entry.enabled), has_key: Boolean(entry.api_key),
    api_key: maskKey(entry.api_key || ''),
  }))
}

export function upsertModelConfig(payload = {}) {
  const name = String(payload.name || '').trim()
  const kind = String(payload.kind || '')
  const base_url = String(payload.base_url || '').trim().replace(/\/$/, '')
  const model = String(payload.model || '').trim()
  if (!/^[\w\u4e00-\u9fff -]{1,40}$/.test(name)) throw Object.assign(new Error('模型名称需为 1-40 位中文/字母/数字/下划线'), { statusCode: 400 })
  if (!MODEL_KINDS.has(kind)) throw Object.assign(new Error('模型类型不正确'), { statusCode: 400 })
  if (!/^https?:\/\/[\w.-]+(:\d+)?([\w./-]*)?$/.test(base_url)) throw Object.assign(new Error('Base URL 不合法'), { statusCode: 400 })
  if (!/^[\w./-]{1,80}$/.test(model)) throw Object.assign(new Error('模型 ID 不合法'), { statusCode: 400 })
  const registry = readModelConfig()
  const id = String(payload.id || '').trim() || crypto.randomUUID().replace(/-/g, '').slice(0, 12)
  const existing = registry.models.find((m) => m.id === id)
  const api_key = String(payload.api_key || '').trim() || (existing ? existing.api_key : '')
  if (!api_key) throw Object.assign(new Error('API Key 不能为空'), { statusCode: 400 })
  const entry = { id, name, kind, base_url, model, api_key, enabled: payload.enabled !== false }
  registry.models = existing ? registry.models.map((m) => (m.id === id ? entry : m)) : [...registry.models, entry]
  writeModelConfig(registry)
  return { id }
}

export function deleteModelConfig(id) {
  const registry = readModelConfig()
  const before = registry.models.length
  registry.models = registry.models.filter((m) => m.id !== id)
  if (registry.models.length !== before) writeModelConfig(registry)
  return registry.models.length !== before
}
