# 量化交易系统 - Vue 前端

基于 Vue 3 + Vuetify 的现代化量化交易前端界面

## 🚀 技术栈

- **Vue 3** - 渐进式 JavaScript 框架
- **Vuetify 3** - Material Design 组件库
- **Vue Router 4** - 官方路由管理器
- **Axios** - HTTP 客户端
- **ECharts** - 数据可视化图表
- **Vite** - 快速构建工具

## 📦 安装依赖

```bash
cd frontend
npm install
```

## 🏃‍♂️ 开发运行

```bash
# 启动开发服务器
npm run dev

# 或直接使用 npx
npx vite --host 0.0.0.0 --port 3000
```

## 🏗️ 生产构建

```bash
# 构建生产版本
npm run build

# 预览构建结果
npm run preview
```

## 🌐 访问地址

- **开发环境**: http://localhost:3001
- **生产环境**: 根据部署配置

## 📱 功能特性

### 1. 仪表板 (Dashboard)
- 📊 实时服务状态监控
- 📈 关键指标展示 (待执行指令、统计记录、活跃品种)
- 🔄 自动刷新数据

### 2. 交易指令管理 (Trade Orders)
- ➕ 发送新的交易指令
- ✅ **智能价格验证**: 自动检查买入/卖出指令的价格合理性
- 📋 查看所有待执行指令
- 🗑️ 批量清空指令

### 3. 统计数据分析 (Statistics)
- 📊 历史数据表格展示
- 📈 价格趋势图表
- 📋 汇总统计指标

### 4. 服务状态监控 (Status)
- ❤️ 健康检查状态
- 📊 详细的服务指标
- 🔄 实时自动刷新 (每5秒)

## 🔧 项目结构

```
frontend/
├── public/                 # 静态资源
├── src/
│   ├── api/               # API 接口
│   │   └── trading.js     # 交易 API
│   ├── components/        # 组件 (预留)
│   ├── plugins/           # 插件配置
│   │   └── vuetify.js     # Vuetify 配置
│   ├── router/            # 路由配置
│   │   └── index.js       # 路由定义
│   ├── views/             # 页面视图
│   │   ├── Dashboard.vue  # 仪表板
│   │   ├── TradeOrders.vue # 交易指令
│   │   ├── Statistics.vue # 统计数据
│   │   └── Status.vue     # 服务状态
│   ├── App.vue            # 根组件
│   ├── main.js            # 入口文件
│   └── style.css          # 全局样式
├── package.json           # 项目配置
├── vite.config.js         # Vite 配置
└── index.html             # HTML 模板
```

## 🔗 API 集成

前端通过代理自动连接到后端 API：

- **后端地址**: http://localhost:8000
- **代理路径**: `/api/*` → `http://localhost:8000/*`

## 🎨 UI 设计

- **Material Design**: 使用 Google Material Design 规范
- **响应式布局**: 支持桌面和移动设备
- **深色主题**: 支持亮色/暗色主题切换
- **中文界面**: 完全本地化的中文界面

## 🔒 安全特性

### 价格验证规则
- **买入指令**: 必须满足 `sl < price < tp`
- **卖出指令**: 必须满足 `tp < price < sl`
- **自动拒绝**: 不符合规则的指令会被服务器拒绝

## 📊 数据可视化

- **ECharts 图表**: 价格趋势线图
- **实时更新**: 图表数据自动刷新
- **交互式**: 支持缩放、拖拽等交互

## 🚀 性能优化

- **Vite 构建**: 快速的冷启动和热重载
- **代码分割**: 自动路由级代码分割
- **懒加载**: 组件按需加载
- **缓存优化**: 浏览器缓存策略

## 🛠️ 开发工具

- **ESLint**: 代码规范检查
- **Vue DevTools**: Vue 开发调试工具
- **热重载**: 修改代码即时预览

## 📝 开发指南

### 添加新页面
1. 在 `src/views/` 创建 Vue 组件
2. 在 `src/router/index.js` 添加路由配置
3. 在 `src/App.vue` 的菜单中添加导航项

### API 调用
```javascript
import { tradingAPI } from '@/api/trading'

// 发送交易指令
await tradingAPI.sendTradeInstructions(instructions)

// 查询统计数据
const stats = await tradingAPI.getStatistics()
```

### 组件开发
```vue
<template>
  <v-card>
    <v-card-title>我的组件</v-card-title>
    <v-card-text>
      <!-- 组件内容 -->
    </v-card-text>
  </v-card>
</template>

<script>
export default {
  name: 'MyComponent',
  setup() {
    // 组件逻辑
    return {
      // 响应式数据
    }
  }
}
</script>
```

## 🔄 与 React 版本对比

| 特性 | Vue 版本 | React 版本 |
|------|----------|------------|
| 框架 | Vue 3 + Composition API | React 18 + Hooks |
| UI库 | Vuetify 3 | Material-UI 5 |
| 路由 | Vue Router 4 | React Router 6 |
| 图表 | ECharts | Recharts |
| 构建 | Vite | Create React App |
| 学习曲线 | 较平缓 | 较陡峭 |
| 性能 | 优秀 | 优秀 |
| 生态 | 成熟 | 庞大 |

## 🎯 优势特点

1. **Vue 生态**: 你熟悉的 Vue 框架和语法
2. **Vuetify**: 功能完整的 Material Design 组件库
3. **TypeScript 支持**: 可选的 TypeScript 支持
4. **开发体验**: 优秀的开发工具和热重载
5. **性能优化**: Vue 3 的优秀性能表现

## 📞 技术支持

如果遇到问题，请检查:
1. 浏览器开发者工具的错误信息
2. 终端的日志输出
3. 确保后端服务正在运行 (http://localhost:8000)

---

**🌟 享受 Vue 开发的乐趣！**