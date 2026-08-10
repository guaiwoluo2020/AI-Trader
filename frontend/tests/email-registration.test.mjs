import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const register = fs.readFileSync(new URL('../src/views/Register.vue', import.meta.url), 'utf8')
const settings = fs.readFileSync(new URL('../src/views/Settings.vue', import.meta.url), 'utf8')
const api = fs.readFileSync(new URL('../src/api/trading.js', import.meta.url), 'utf8')

test('registration requires an emailed six digit verification code', () => {
  assert.match(register, /sendRegistrationCode/)
  assert.match(register, /verification_code: verificationCode\.value/)
  assert.match(register, /\^\\d\{6\}\$/)
  assert.match(register, /resendCountdown/)
  assert.match(api, /auth\/email-code/)
})

test('admin can configure encrypted SMTP delivery without password echo', () => {
  assert.match(settings, /注册邮件服务/)
  assert.match(settings, /SMTP 密码加密存储且不会回显/)
  assert.match(settings, /saveEmailConfig/)
  assert.match(settings, /testEmailConfig/)
  assert.match(api, /auth\/admin\/email-config/)
})
