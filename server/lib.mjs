import { spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const VENV_PYTHON = path.join(ROOT, '.venv', 'bin', 'python')
const PYTHON = process.env.FINANCE_PYTHON || (fs.existsSync(VENV_PYTHON) ? VENV_PYTHON : 'python3')
const SCRIPT = path.join(ROOT, 'scripts', 'finance.py')
const MARKETS = new Set(['auto', 'cn', 'hk', 'us', 'crypto', 'etf', 'fund', 'future', 'metal'])
const PERIODS = new Set(['1d:5m', '5d:15m', '5d:30m', '1mo:5m', '1mo:1d', '3mo:1d', '6mo:1d', '1y:1d', '2y:1d', '5y:1d'])

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
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
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
