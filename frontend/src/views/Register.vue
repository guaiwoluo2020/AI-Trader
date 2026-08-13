<template>
  <div class="register-page">
    <div class="register-shell">
      <section class="story-panel">
        <router-link class="brand" to="/login">AI TRADER</router-link>
        <div class="story-content">
          <span class="eyebrow">PRIVATE RESEARCH CIRCLE</span>
          <h1>受邀加入技术验证小组</h1>
          <p>
            本站不对公众开放，仅限站长本人及受邀私人小圈子进行量化交易技术验证。
          </p>

          <ol>
            <li>
              <span>01</span>
              验证邀请资格
            </li>
            <li>
              <span>02</span>
              验证常用邮箱
            </li>
            <li>
              <span>03</span>
              创建独立验证账号
            </li>
          </ol>
        </div>
        <div class="security-line">
          <v-icon icon="mdi-shield-lock-outline" size="18" />
          普通成员使用邮箱验证码登录，不设置账户密码
        </div>
      </section>

      <main class="form-panel">
        <div class="form-wrap">
          <div class="mobile-brand">AI TRADER</div>
          <span class="form-kicker">NEW ACCOUNT</span>
          <h2>注册新账号</h2>
          <p class="form-intro">输入邀请码，验证邮箱后创建私人测试账号。</p>

          <v-alert
            v-if="errorMessage"
            type="error"
            variant="tonal"
            class="mb-5"
            closable
            @click:close="errorMessage = ''"
          >
            {{ errorMessage }}
          </v-alert>

          <v-form @submit.prevent="handleRegister">
            <label class="field-label" for="register-invite">邀请码</label>
            <v-text-field
              id="register-invite"
              v-model="invitationCode"
              placeholder="输入邀请码，或通过邀请链接自动填写"
              prepend-inner-icon="mdi-ticket-confirmation-outline"
              variant="outlined"
              autocomplete="one-time-code"
              :disabled="loading"
              :error-messages="invitationError"
              class="mb-2"
              @blur="invitationTouched = true"
            />

            <label class="field-label" for="register-email">邮箱</label>
            <v-text-field
              id="register-email"
              v-model="email"
              placeholder="用于接收 6 位注册验证码"
              prepend-inner-icon="mdi-email-outline"
              variant="outlined"
              autocomplete="email"
              :disabled="loading"
              :error-messages="emailError"
              class="mb-2"
              @blur="emailTouched = true"
            />

            <label class="field-label" for="register-code">邮箱验证码</label>
            <div class="verification-row">
              <v-text-field
                id="register-code"
                v-model="verificationCode"
                placeholder="6 位数字"
                prepend-inner-icon="mdi-shield-key-outline"
                variant="outlined"
                inputmode="numeric"
                maxlength="6"
                :disabled="loading"
                :error-messages="codeError"
                @blur="codeTouched = true"
              />
              <v-btn
                color="primary"
                variant="tonal"
                height="56"
                :loading="codeSending"
                :disabled="!emailValid || !invitationValid || resendCountdown > 0 || loading"
                @click="sendVerificationCode"
              >
                {{ resendCountdown > 0 ? `${resendCountdown}s 后重发` : '发送验证码' }}
              </v-btn>
            </div>

            <label class="field-label" for="register-username">用户名</label>
            <v-text-field
              id="register-username"
              v-model="username"
              placeholder="3-32 位字母、数字、_ 或 -"
              prepend-inner-icon="mdi-account-outline"
              variant="outlined"
              autocomplete="username"
              :disabled="loading"
              :error-messages="usernameError"
              class="mb-2"
              @blur="usernameTouched = true"
            />

            <div class="registration-consent">
              <v-checkbox
                v-model="acceptedServiceNotice"
                color="primary"
                density="compact"
                hide-details
                :disabled="loading"
                @update:model-value="consentTouched = true"
              >
                <template #label>
                  <span>我已阅读并同意：本网站仅限站长本人及受邀私人小圈子进行技术验证，不面向公众开放，仅接受邀请码或邀请链接加入。平台仅提供数据与技术测试服务，所有分析、信号和输出仅供测试参考，不构成投资建议、交易邀约或收益承诺；使用者独立承担交易及资金风险。</span>
                </template>
              </v-checkbox>
              <p v-if="consentError" class="consent-error">{{ consentError }}</p>
            </div>

            <v-btn
              type="submit"
              color="primary"
              size="x-large"
              block
              :loading="loading"
              :disabled="!acceptedServiceNotice"
              class="register-button"
            >
              验证并创建账号
            </v-btn>
          </v-form>

          <p class="login-link">
            已经有账号？
            <router-link to="/login">返回登录</router-link>
          </p>
        </div>
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { authAPI } from '../api/trading'

const router = useRouter()
const route = useRoute()
const invitationCode = ref(String(route.query.invite || '').trim().toUpperCase())
const email = ref('')
const verificationCode = ref('')
const username = ref('')
const invitationTouched = ref(Boolean(route.query.invite))
const emailTouched = ref(false)
const codeTouched = ref(false)
const usernameTouched = ref(false)
const consentTouched = ref(false)
const acceptedServiceNotice = ref(false)
const loading = ref(false)
const codeSending = ref(false)
const resendCountdown = ref(0)
const errorMessage = ref('')
let countdownTimer = null

const normalizedEmail = computed(() => email.value.trim().toLowerCase())
const normalizedInvitationCode = computed(() => invitationCode.value.trim().toUpperCase())
const invitationValid = computed(() => /^[A-Z0-9]{8,32}$/.test(normalizedInvitationCode.value))
const emailValid = computed(() => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedEmail.value))
const codeValid = computed(() => /^\d{6}$/.test(verificationCode.value))
const normalizedUsername = computed(() => username.value.trim().toLowerCase())
const usernameValid = computed(() => /^[a-z0-9_-]{3,32}$/.test(normalizedUsername.value))
const invitationError = computed(() =>
  invitationTouched.value && !invitationValid.value ? '请输入有效邀请码' : '',
)

const emailError = computed(() =>
  emailTouched.value && !emailValid.value ? '请输入有效的常用邮箱地址' : '',
)
const codeError = computed(() =>
  codeTouched.value && !codeValid.value ? '请输入邮件中的 6 位验证码' : '',
)
const usernameError = computed(() =>
  usernameTouched.value && !usernameValid.value
    ? '请输入 3-32 位用户名，仅支持字母、数字、下划线和短横线'
    : '',
)
const consentError = computed(() =>
  consentTouched.value && !acceptedServiceNotice.value ? '请先阅读并同意平台服务说明' : '',
)

async function handleRegister() {
  invitationTouched.value = true
  emailTouched.value = true
  codeTouched.value = true
  usernameTouched.value = true
  consentTouched.value = true
  errorMessage.value = ''

  if (
    !invitationValid.value ||
    !emailValid.value ||
    !codeValid.value ||
    !usernameValid.value ||
    !acceptedServiceNotice.value
  ) {
    return
  }

  loading.value = true
  try {
    const registerResult = await authAPI.register({
      email: normalizedEmail.value,
      verification_code: verificationCode.value,
      username: normalizedUsername.value,
      invitation_code: normalizedInvitationCode.value,
      accepted_private_use_terms: true,
    })
    router.replace(registerResult.next_path || '/mt5-setup')
  } catch (error) {
    errorMessage.value = error.response?.data?.detail || '注册失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

async function sendVerificationCode() {
  invitationTouched.value = true
  emailTouched.value = true
  errorMessage.value = ''
  if (!emailValid.value || !invitationValid.value) return
  codeSending.value = true
  try {
    const result = await authAPI.sendRegistrationCode(
      normalizedEmail.value, normalizedInvitationCode.value
    )
    resendCountdown.value = Number(result.resend_in || 60)
    clearInterval(countdownTimer)
    countdownTimer = setInterval(() => {
      resendCountdown.value = Math.max(0, resendCountdown.value - 1)
      if (!resendCountdown.value) clearInterval(countdownTimer)
    }, 1000)
  } catch (error) {
    errorMessage.value = error.response?.data?.detail || '验证码发送失败，请稍后重试'
  } finally {
    codeSending.value = false
  }
}

onUnmounted(() => clearInterval(countdownTimer))
</script>

<style scoped>
.register-page {
  min-height: 100vh;
  padding: 32px;
  color: #19251e;
  background:
    radial-gradient(circle at 85% 10%, rgba(201, 151, 45, 0.2), transparent 26rem),
    #e9eee7;
}

.register-shell {
  display: grid;
  grid-template-columns: minmax(360px, 0.9fr) minmax(460px, 1.1fr);
  max-width: 1120px;
  min-height: calc(100vh - 64px);
  margin: 0 auto;
  overflow: hidden;
  border-radius: 28px;
  box-shadow: 0 30px 80px rgba(24, 43, 31, 0.18);
}

.story-panel {
  display: flex;
  flex-direction: column;
  padding: 44px;
  color: #f4f0df;
  background:
    linear-gradient(150deg, rgba(255, 255, 255, 0.05), transparent 40%),
    #17251c;
}

.brand,
.mobile-brand {
  color: #c9972d;
  font-family: "Avenir Next Condensed", "DIN Condensed", sans-serif;
  font-weight: 900;
  letter-spacing: 0.18em;
  text-decoration: none;
}

.story-content {
  margin: auto 0;
}

.eyebrow,
.form-kicker {
  color: #c9972d;
  font-size: 0.73rem;
  font-weight: 800;
  letter-spacing: 0.19em;
}

.story-content h1 {
  margin: 14px 0 18px;
  font-family: "Songti SC", "STSong", serif;
  font-size: clamp(2.4rem, 4vw, 4rem);
  line-height: 1.08;
}

.story-content > p {
  max-width: 480px;
  color: #bfc9c0;
  line-height: 1.8;
}

.story-content ol {
  margin: 36px 0 0;
  padding: 0;
  list-style: none;
}

.story-content li {
  display: flex;
  align-items: center;
  gap: 15px;
  padding: 13px 0;
  color: #dfe5df;
  border-top: 1px solid rgba(255, 255, 255, 0.11);
}

.story-content li span {
  color: #c9972d;
  font-family: "Avenir Next Condensed", sans-serif;
  font-weight: 800;
}

.security-line {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #9eaaa0;
  font-size: 0.8rem;
}

.form-panel {
  display: grid;
  place-items: center;
  padding: 52px 68px;
  background: #fffef8;
}

.form-wrap {
  width: 100%;
  max-width: 460px;
}

.mobile-brand {
  display: none;
}

.form-wrap h2 {
  margin: 8px 0;
  font-family: "Songti SC", "STSong", serif;
  font-size: 2.3rem;
}

.form-intro {
  margin: 0 0 30px;
  color: #6d766f;
}

.field-label {
  display: block;
  margin: 0 0 7px 2px;
  font-size: 0.86rem;
  font-weight: 700;
}

.verification-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
  gap: 10px;
  margin-bottom: 2px;
}

.strength-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr) auto;
  align-items: center;
  gap: 7px;
  min-height: 24px;
  margin: -3px 2px 15px;
}

.strength-bar {
  height: 4px;
  border-radius: 99px;
  background: #dce2db;
}

.strength-bar.active {
  background: #c9972d;
}

.strength-row small {
  min-width: 54px;
  color: #778079;
  text-align: right;
}

.registration-consent {
  margin: 2px 0 8px;
}

.registration-consent :deep(.v-label) {
  color: #5d685f;
  font-size: 0.8rem;
  line-height: 1.6;
}

.consent-error {
  margin: -2px 0 0 34px;
  color: #b3261e;
  font-size: 0.75rem;
}

.register-button {
  margin-top: 14px;
  font-weight: 800;
  letter-spacing: 0.02em;
}

.login-link {
  margin: 24px 0 0;
  color: #778079;
  text-align: center;
}

.login-link a {
  color: #1d6840;
  font-weight: 800;
  text-decoration: none;
}

@media (max-width: 850px) {
  .register-page {
    padding: 0;
  }

  .register-shell {
    display: block;
    min-height: 100vh;
    border-radius: 0;
  }

  .story-panel {
    display: none;
  }

  .form-panel {
    min-height: 100vh;
    padding: 38px 22px;
  }

  .mobile-brand {
    display: block;
    margin-bottom: 38px;
  }
}

@media (max-width: 480px) {
  .verification-row {
    grid-template-columns: 1fr;
    margin-bottom: 12px;
  }
}
</style>
