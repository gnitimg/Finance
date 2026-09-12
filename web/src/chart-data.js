function finiteNumber(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

export function rollingMean(values, windowSize = 20) {
  let sum = 0
  const queue = []
  return values.map((value) => {
    const numeric = finiteNumber(value)
    if (numeric === null) return null
    queue.push(numeric)
    sum += numeric
    if (queue.length > windowSize) sum -= queue.shift()
    return queue.length === windowSize ? sum / windowSize : null
  })
}

export function buildChartData(bars = [], forecast = {}) {
  const cleanBars = Array.isArray(bars) ? bars.filter((bar) => bar?.time && finiteNumber(bar.close) !== null) : []
  const actualAxis = cleanBars.map((bar) => bar.time)
  const closes = cleanBars.map((bar) => finiteNumber(bar.close))
  const volumes = cleanBars.map((bar) => finiteNumber(bar.volume) || 0)
  // Fit view: each point is the model's prediction from its own bar (one-shot),
  // so the line shows real per-step fit instead of a free-running drift.
  const historicalSource = forecast?.series?.one_shot_predicted || forecast?.series?.predicted || []
  const historicalMap = new Map(historicalSource.map((point) => [point.time, finiteNumber(point.value)]))
  const backtest = cleanBars.map((bar) => historicalMap.get(bar.time) ?? null)
  const ma20 = rollingMean(closes, 20)
  const next = forecast?.next_forecast
  const path = Array.isArray(forecast?.forward_series) ? forecast.forward_series : []
  const futurePoints = path
    .filter((point) => point?.time && point.kind !== 'origin' && Number(point.step) > 0 && !actualAxis.includes(point.time) && finiteNumber(point.value) !== null)
    .sort((left, right) => new Date(left.time) - new Date(right.time))
  if (!futurePoints.length && next?.target_time && finiteNumber(next?.predicted_price) !== null) {
    futurePoints.push({ time: next.target_time, value: finiteNumber(next.predicted_price), step: next.horizon_bars })
  }
  const hasForward = cleanBars.length > 0 && futurePoints.length > 0
  const horizonBars = Math.max(1, Number(next?.horizon_bars) || 1)
  const futureTimes = futurePoints.map((point) => point.time)
  const axis = actualAxis.concat(futureTimes)
  const pad = Array(futureTimes.length).fill(null)
  const forward = Array(axis.length).fill(null)
  const futureLower = Array(axis.length).fill(null)
  const futureBand = Array(axis.length).fill(null)
  const intervals = {}

  if (hasForward) {
    const origin = finiteNumber(next?.origin_price) ?? closes.at(-1)
    forward[cleanBars.length - 1] = origin
    futureLower[cleanBars.length - 1] = origin
    futureBand[cleanBars.length - 1] = 0
    futurePoints.forEach((point, index) => {
      const axisIndex = cleanBars.length + index
      const value = finiteNumber(point.value)
      const lower = finiteNumber(point.lower)
      const upper = finiteNumber(point.upper)
      forward[axisIndex] = value
      if (lower !== null && upper !== null && upper >= lower) {
        futureLower[axisIndex] = lower
        futureBand[axisIndex] = upper - lower
        intervals[point.time] = { lower, upper }
      }
    })
  }

  return {
    axis,
    actualAxis,
    closes: closes.concat(pad),
    volumes: volumes.concat(pad),
    backtest: backtest.concat(pad),
    ma20: ma20.concat(pad),
    forward,
    futureLower,
    futureBand,
    future: {
      enabled: hasForward,
      startKey: hasForward ? actualAxis.at(-1) : null,
      endKey: hasForward ? futureTimes.at(-1) : null,
      timestamps: futureTimes,
      horizonBars,
      horizonLabel: next?.horizon_label || forecast?.horizon_label || `${horizonBars} 个 BAR`,
      intervals,
    },
  }
}
