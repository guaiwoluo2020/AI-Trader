# 贸易文档助手 MCP

这是一个本地 stdio MCP 连接器，不会把 App Secret 上传到第三方服务。

## 安装

```bash
cd feishu-mcp
npm install
```

在本机设置环境变量（不要发送到聊天）：

```bash
export FEISHU_APP_ID='cli_aa30f44f16789bda'
export FEISHU_APP_SECRET='从飞书“凭证与基础信息”复制的 Secret'
```

然后运行：

```bash
npm start
```

首次使用 `feishu_list_files` 时，把飞书“贸易”文件夹 URL 中的 folder token 作为参数传入。连接器提供列出文件、读取文档、创建文档、追加文本四个工具。
