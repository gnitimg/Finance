import assert from 'node:assert/strict'
import test from 'node:test'

import { buildChartData, rollingMean } from '../web/src/chart-data.js'

test('chart axis keeps observed trading bars and appends real forecast timestamps', () => {
  const bars = [
    { time: '2026-09-11T03:00:00Z', close: 100, volume: 10 },
    { time: '2026-09-11T07:00:00Z', close: 101, volume: 12 },
    { time: '2026-09-14T01:30:00Z', close: 102, volume: 14 },
  ]
  const result = buildChartData(bars, {
    series: { predicted: [{ time: bars[1].time, value: 100.5 }] },
    forward_series: [
      { time: bars[2].time, value: 102, step: 0 },
      { time: '2026-09-14T01:45:00Z', value: 103, lower: 102.5, upper: 103.5, step: 1 },
      { time: '2026-09-14T02:00:00Z', value: 104, lower: 103, upper: 105, step: 2 },
    ],
    next_forecast: { origin_price: 102, target_time: '2026-09-14T02:00:00Z', predicted_price: 104, horizon_bars: 2, horizon_label: '30 分钟' },
  })

  assert.equal(result.actualAxis.length, 3)
  assert.equal(result.axis.length, 5)
  assert.deepEqual(result.closes.slice(0, 3), [100, 101, 102])
  assert.equal(result.backtest[1], 100.5)
  assert.equal(result.forward[2], 102)
  assert.equal(result.forward.at(-1), 104)
  assert.equal(result.futureLower.at(-1), 103)
  assert.equal(result.futureBand.at(-1), 2)
  assert.deepEqual(result.future.intervals['2026-09-14T02:00:00Z'], { lower: 103, upper: 105 })
  assert.equal(result.future.horizonLabel, '30 分钟')
  assert.deepEqual(result.future.timestamps, ['2026-09-14T01:45:00Z', '2026-09-14T02:00:00Z'])
})

test('rolling MA20 is a real rolling series rather than a constant current value', () => {
  const result = rollingMean(Array.from({ length: 21 }, (_, index) => index + 1), 20)
  assert.equal(result[18], null)
  assert.equal(result[19], 10.5)
  assert.equal(result[20], 11.5)
})

test('chart does not create future slots without a valid forward forecast', () => {
  const bars = [{ time: '2026-09-11T03:00:00Z', close: 100, volume: 10 }]
  const result = buildChartData(bars, { status: 'insufficient_data', series: { predicted: [] } })
  assert.deepEqual(result.axis, [bars[0].time])
  assert.equal(result.future.enabled, false)
})

test('long-period charts use exact future dates without visual padding', () => {
  const bars = Array.from({ length: 100 }, (_, index) => ({ time: `bar-${index}`, close: 100 + index, volume: 10 }))
  const result = buildChartData(bars, { next_forecast: { target_time: '2026-09-14T00:00:00Z', predicted_price: 202, horizon_bars: 3 } })
  assert.equal(result.axis.length, 101)
})
