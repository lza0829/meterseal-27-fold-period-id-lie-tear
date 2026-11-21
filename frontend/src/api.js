const base = '/api'

async function req(path, opts = {}) {
  const res = await fetch(base + path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  })
  const text = await res.text()
  let data
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!res.ok) {
    const msg = (data && data.detail) || text || res.statusText
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  }
  return data
}

export const api = {
  overview: () => req('/overview'),
  consistency: () => req('/consistency'),
  meters: () => req('/meters'),
  estimates: (periodId) => req('/estimates' + (periodId ? `?period_id=${periodId}` : '')),
  actuals: (periodId) => req('/actuals' + (periodId ? `?period_id=${periodId}` : '')),
  postEstimate: (body) => req('/estimates', { method: 'POST', body: JSON.stringify(body) }),
  postActual: (body) => req('/actuals', { method: 'POST', body: JSON.stringify(body) }),
  periods: () => req('/periods'),
  align: (period_id) => req('/align', { method: 'POST', body: JSON.stringify({ period_id }) }),
  batches: (periodId) => req('/batches' + (periodId ? `?period_id=${periodId}` : '')),
  variances: (q) => {
    const sp = new URLSearchParams(q || {})
    const s = sp.toString()
    return req('/variances' + (s ? `?${s}` : ''))
  },
  setFate: (id, fate) => req(`/variances/${id}/fate`, { method: 'POST', body: JSON.stringify({ fate }) }),
  seal: (body) => req('/seal', { method: 'POST', body: JSON.stringify(body) }),
  receipts: (q) => {
    const sp = new URLSearchParams(q || {})
    const s = sp.toString()
    return req('/receipts' + (s ? `?${s}` : ''))
  },
  settings: () => req('/settings'),
  putSetting: (key, value) => req(`/settings/${key}`, { method: 'PUT', body: JSON.stringify({ value }) }),
}
