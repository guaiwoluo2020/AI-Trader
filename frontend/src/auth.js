import { reactive } from 'vue'

const STORAGE_KEY = 'ai-trader-auth'
const ADMIN_SESSION_KEY = 'ai-trader-admin-session'

function loadStoredSession() {
  if (typeof window === 'undefined') {
    return { token: '', user: null }
  }

  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) {
      return { token: '', user: null }
    }

    const parsed = JSON.parse(raw)
    return {
      token: parsed.token || '',
      user: parsed.user || null,
    }
  } catch (error) {
    console.error('Failed to load auth session:', error)
    return { token: '', user: null }
  }
}

const storedSession = loadStoredSession()

export const authState = reactive({
  token: storedSession.token,
  user: storedSession.user,
})

export function setAuthSession(session) {
  authState.token = session.token
  authState.user = session.user

  if (typeof window !== 'undefined') {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        token: session.token,
        user: session.user,
      })
    )
  }
}

export function clearAuthSession() {
  authState.token = ''
  authState.user = null

  if (typeof window !== 'undefined') {
    window.localStorage.removeItem(STORAGE_KEY)
  }
}

export function saveAdminSessionForView() {
  if (typeof window !== 'undefined' && authState.user?.role === 'admin') {
    window.sessionStorage.setItem(ADMIN_SESSION_KEY, JSON.stringify({
      token: authState.token,
      user: authState.user,
    }))
  }
}

export function restoreAdminSession() {
  if (typeof window === 'undefined') return false
  try {
    const raw = window.sessionStorage.getItem(ADMIN_SESSION_KEY)
    if (!raw) return false
    const session = JSON.parse(raw)
    setAuthSession(session)
    window.sessionStorage.removeItem(ADMIN_SESSION_KEY)
    return true
  } catch (_) {
    return false
  }
}

export function getAuthToken() {
  return authState.token
}

export function isAuthenticated() {
  return Boolean(authState.token)
}
