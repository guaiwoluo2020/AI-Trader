<template>
  <div class="login-page">
    <v-container class="fill-height">
      <v-row align="center" justify="center">
        <v-col cols="12" sm="10" md="6" lg="4">
          <v-card class="login-card" elevation="16">
            <v-card-title class="text-h4 font-weight-bold">AI Trader</v-card-title>
            <v-card-subtitle class="login-subtitle">
              登录后才能查看交易面板和手动操作接口
            </v-card-subtitle>

            <v-card-text>
              <v-alert
                v-if="errorMessage"
                type="error"
                variant="tonal"
                class="mb-4"
              >
                {{ errorMessage }}
              </v-alert>

              <v-form @submit.prevent="handleLogin">
                <v-text-field
                  v-model="username"
                  label="用户名"
                  prepend-inner-icon="mdi-account-outline"
                  variant="outlined"
                  class="mb-3"
                  :disabled="loading"
                  required
                />
                <v-text-field
                  v-model="password"
                  label="密码"
                  prepend-inner-icon="mdi-lock-outline"
                  variant="outlined"
                  :type="showPassword ? 'text' : 'password'"
                  :append-inner-icon="showPassword ? 'mdi-eye-off' : 'mdi-eye'"
                  :disabled="loading"
                  required
                  @click:append-inner="showPassword = !showPassword"
                />
                <v-btn
                  type="submit"
                  color="primary"
                  size="large"
                  block
                  :loading="loading"
                  class="mt-4"
                >
                  登录
                </v-btn>
              </v-form>
            </v-card-text>

            <v-card-text class="login-tip">
              默认管理员账号：`admin` / `admin123456`
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>
    </v-container>
  </div>
</template>

<script>
import { authAPI } from '../api/trading'

export default {
  name: 'Login',
  data() {
    return {
      username: 'admin',
      password: 'admin123456',
      showPassword: false,
      loading: false,
      errorMessage: '',
    }
  },
  methods: {
    async handleLogin() {
      this.loading = true
      this.errorMessage = ''

      try {
        await authAPI.login({
          username: this.username.trim(),
          password: this.password,
        })

        const redirect = this.$route.query.redirect || '/'
        this.$router.push(redirect)
      } catch (error) {
        this.errorMessage = error.response?.data?.detail || '登录失败，请检查用户名和密码'
      } finally {
        this.loading = false
      }
    },
  },
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  background:
    radial-gradient(circle at top, rgba(25, 118, 210, 0.2), transparent 35%),
    linear-gradient(135deg, #06121f 0%, #102b3f 48%, #163d5c 100%);
}

.login-card {
  border-radius: 24px;
  padding: 16px;
}

.login-subtitle {
  margin-top: 8px;
  white-space: normal;
}

.login-tip {
  color: rgba(0, 0, 0, 0.66);
  font-size: 0.9rem;
}
</style>
