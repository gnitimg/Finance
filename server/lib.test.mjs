import test from 'node:test'
import assert from 'node:assert/strict'
import { compactOverviewData, parseAssetList, validateAsset, validateMonitorThresholds, validatePeriod } from './lib.mjs'

test('asset validation normalizes safe symbols', () => {
  assert.deepEqual(validateAsset('US', 'nvda'), { market: 'us', symbol: 'NVDA' })
  assert.deepEqual(validateAsset('future', 'es=f'), { market: 'future', symbol: 'ES=F' })
  assert.deepEqual(validateAsset('metal', 'gold'), { market: 'metal', symbol: 'GOLD' })
  assert.throws(() => validateAsset('us', 'NVDA;echo'), /invalid symbol/)
})

test('period validation uses an explicit allowlist', () => {
  assert.deepEqual(validatePeriod('1d', '5m'), { range: '1d', interval: '5m' })
  assert.throws(() => validatePeriod('max', '1m'), /invalid/)
})

test('asset list is bounded', () => {
  const items = parseAssetList('us:NVDA,cn:601619,crypto:USDT')
  assert.deepEqual(items, ['us:NVDA', 'cn:601619', 'crypto:USDT'])
})

test('monitor thresholds are bounded and normalized', () => {
  assert.deepEqual(validateMonitorThresholds({ forecast: '0.8', price: '2.5', volume: '1.9' }), { forecast_pct: 0.8, price_change_pct: 2.5, volume_ratio: 1.9 })
  assert.throws(() => validateMonitorThresholds({ volume: '0.4' }), /invalid monitor threshold/)
})

test('overview data excludes historical bars and analysis payloads', () => {
  const compact = compactOverviewData({
    asset: { symbol: 'NVDA' },
    quote: { price: 100 },
    cache: { cached: false },
    provider_timing: { elapsed_ms: 10 },
    history: [{ close: 99 }],
    technical: { score: 25 },
  })
  assert.deepEqual(Object.keys(compact), ['asset', 'quote', 'cache', 'provider_timing'])
  assert.equal(compact.asset.symbol, 'NVDA')
  assert.equal(compact.history, undefined)
})
