import assert from 'node:assert/strict'

async function loadHelper() {
  try {
    const mod = await import('../src/api/auth-helpers.js')
    return mod.applyAuthToRequestConfig
  } catch {
    return null
  }
}

const applyAuthToRequestConfig = await loadHelper()

assert.equal(
  typeof applyAuthToRequestConfig,
  'function',
  'applyAuthToRequestConfig should exist'
)

const withToken = applyAuthToRequestConfig({ headers: {} }, 'test-token')
assert.equal(withToken.headers.Authorization, 'Bearer test-token')

const withoutToken = applyAuthToRequestConfig({ headers: {} }, '')
assert.equal(withoutToken.headers.Authorization, undefined)

console.log('auth-request-config test passed')
