const runtimeEnv = import.meta.env || {}

export const API_BASE_URL =
  runtimeEnv.VITE_API_BASE_URL ||
  (runtimeEnv.DEV ? 'http://localhost:8000' : '/api')

export function getMarketWebSocketUrl() {
  if (runtimeEnv.VITE_MARKET_WS_URL) {
    return runtimeEnv.VITE_MARKET_WS_URL
  }

  if (/^https?:\/\//.test(API_BASE_URL)) {
    const apiUrl = new URL(API_BASE_URL)
    const protocol = apiUrl.protocol === 'https:' ? 'wss:' : 'ws:'
    const basePath = apiUrl.pathname.replace(/\/$/, '')
    return `${protocol}//${apiUrl.host}${basePath}/ws/market`
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const basePath = API_BASE_URL.replace(/\/$/, '')
  return `${protocol}//${window.location.host}${basePath}/ws/market`
}
