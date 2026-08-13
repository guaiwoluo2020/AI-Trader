<template>
  <div class="login-page">
    <v-container class="fill-height">
      <v-row align="center" justify="center">
        <v-col cols="12" sm="10" md="6" lg="4">
          <v-card class="login-card" elevation="16">
            <div class="login-mark">PRIVATE LAB</div>
            <v-card-title class="text-h4 font-weight-bold">AI Trader</v-card-title>
            <v-card-subtitle class="login-subtitle">
              受邀成员使用邮箱验证码登录
            </v-card-subtitle>

            <v-card-text>
              <v-alert v-if="errorMessage" type="error" variant="tonal" class="mb-4">
                {{ errorMessage }}
              </v-alert>

              <v-form @submit.prevent="handleEmailLogin">
                <v-text-field
                  v-model="email"
                  label="注册邮箱"
                  prepend-inner-icon="mdi-email-outline"
                  variant="outlined"
                  autocomplete="email"
                  :disabled="loading"
                  required
                />
                <div class="code-row">
                  <v-text-field
                    v-model="verificationCode"
                    label="6 位验证码"
                    prepend-inner-icon="mdi-shield-key-outline"
                    variant="outlined"
                    inputmode="numeric"
                    maxlength="6"
                    :disabled="loading"
                    required
                  />
                  <v-btn
                    color="primary"
                    variant="tonal"
                    height="56"
                    :loading="codeSending"
                    :disabled="!emailValid || resendCountdown > 0 || loading"
                    @click="sendCode"
                  >
                    {{ resendCountdown ? `${resendCountdown}s` : '获取验证码' }}
                  </v-btn>
                </div>
                <v-btn type="submit" color="primary" size="large" block :loading="loading" class="mt-3">
                  邮箱验证登录
                </v-btn>
              </v-form>

            </v-card-text>

            <v-card-text class="login-footer">
              <span>验证码 3 分钟内有效</span>
              <span>仅接受 <router-link to="/register">邀请码注册</router-link></span>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>
    </v-container>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { authAPI } from '../api/trading'

const route = useRoute()
const router = useRouter()
const email = ref('')
const verificationCode = ref('')
const loading = ref(false)
const codeSending = ref(false)
const resendCountdown = ref(0)
const errorMessage = ref('')
let timer = null

const normalizedEmail = computed(() => email.value.trim().toLowerCase())
const emailValid = computed(() => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedEmail.value))

function finishLogin(result) {
  router.push(route.query.redirect || result.next_path || '/')
}

async function sendCode() {
  if (!emailValid.value) return
  codeSending.value = true
  errorMessage.value = ''
  try {
    const result = await authAPI.sendLoginCode(normalizedEmail.value)
    resendCountdown.value = Number(result.resend_in || 60)
    clearInterval(timer)
    timer = setInterval(() => {
      resendCountdown.value = Math.max(0, resendCountdown.value - 1)
      if (!resendCountdown.value) clearInterval(timer)
    }, 1000)
  } catch (error) {
    errorMessage.value = error.response?.data?.detail || '验证码发送失败'
  } finally {
    codeSending.value = false
  }
}

async function handleEmailLogin() {
  if (!emailValid.value || !/^\d{6}$/.test(verificationCode.value)) {
    errorMessage.value = '请输入有效邮箱和 6 位验证码'
    return
  }
  loading.value = true
  errorMessage.value = ''
  try {
    finishLogin(await authAPI.loginWithEmail({
      email: normalizedEmail.value,
      verification_code: verificationCode.value,
    }))
  } catch (error) {
    errorMessage.value = error.response?.data?.detail || '登录失败，请检查验证码'
  } finally {
    loading.value = false
  }
}

onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.login-page { min-height: 100vh; background: radial-gradient(circle at 18% 12%, rgba(211, 164, 62, .18), transparent 28rem), linear-gradient(135deg, #071912 0%, #12372b 55%, #1d4d3c 100%); }
.login-card { padding: 18px; border-radius: 24px; border: 1px solid rgba(255,255,255,.5); }
.login-mark { display: inline-flex; margin: 8px 16px 14px; padding: 5px 10px; border-radius: 20px; color: #176b4d; background: #e3f1ea; font-size: .7rem; font-weight: 800; letter-spacing: .12em; }
.login-subtitle { margin-top: 8px; white-space: normal; }
.code-row { display: grid; grid-template-columns: minmax(0, 1fr) 116px; gap: 10px; }
.login-footer { display: flex; justify-content: space-between; gap: 12px; color: rgba(0,0,0,.6); font-size: .86rem; }
.login-footer a { color: #176b4d; font-weight: 700; text-decoration: none; }
@media (max-width: 520px) { .code-row { grid-template-columns: 1fr; }.login-footer { align-items: flex-start; flex-direction: column; } }
</style>
