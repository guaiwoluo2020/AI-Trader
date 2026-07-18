import { reactive } from 'vue'

const STORAGE_KEY = 'ai-trader-auth'

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

export function getAuthToken() {
  return authState.token
}

export function isAuthenticated() {
  return Boolean(authState.token)
}
