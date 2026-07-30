import { clearAuthSession, getAuthToken } from '../auth.js'

export function applyAuthToRequestConfig(config, token = getAuthToken()) {
  const nextConfig = { ...config, headers: { ...(config.headers || {}) } }

  if (token) {
    nextConfig.headers.Authorization = `Bearer ${token}`
  }

  return nextConfig
}

export function handleAuthError(error) {
  if (error.response?.status === 401) {
    clearAuthSession()
    if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
      window.location.href = '/login'
    }
  }

  return Promise.reject(error)
}
