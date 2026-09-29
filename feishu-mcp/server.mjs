import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const appId = process.env.FEISHU_APP_ID;
const appSecret = process.env.FEISHU_APP_SECRET;
const base = process.env.FEISHU_BASE_URL || "https://open.feishu.cn";
if (!appId || !appSecret) throw new Error("FEISHU_APP_ID and FEISHU_APP_SECRET are required");

let tokenCache = { value: "", expiresAt: 0 };
async function tenantToken() {
  if (tokenCache.value && Date.now() < tokenCache.expiresAt) return tokenCache.value;
  const r = await fetch(`${base}/open-apis/auth/v3/tenant_access_token/internal`, {
    method: "POST", headers: { "content-type": "application/json; charset=utf-8" },
    body: JSON.stringify({ app_id: appId, app_secret: appSecret })
  });
  const body = await r.json();
  if (!r.ok || body.code) throw new Error(`Feishu auth failed: ${body.msg || r.status}`);
  tokenCache = { value: body.tenant_access_token, expiresAt: Date.now() + (body.expire - 120) * 1000 };
  return tokenCache.value;
}
async function api(path, options = {}) {
  const r = await fetch(`${base}${path}`, {
    ...options, headers: { authorization: `Bearer ${await tenantToken()}`, "content-type": "application/json; charset=utf-8", ...(options.headers || {}) }
  });
  const body = await r.json();
  if (!r.ok || body.code) throw new Error(`Feishu API failed: ${body.msg || r.status}`);
  return body.data ?? body;
}
const text = (value) => ({ content: [{ type: "text", text: typeof value === "string" ? value : JSON.stringify(value, null, 2) }] });

const server = new McpServer({ name: "feishu-trade-docs", version: "0.1.0" });
server.registerTool("feishu_list_files", {
  description: "列出飞书云盘文件夹中的文件。传入贸易文件夹的 folder_token。",
  inputSchema: { folder_token: z.string().min(1), page_size: z.number().int().min(1).max(200).default(50) }
}, async ({ folder_token, page_size }) => text(await api(`/open-apis/drive/v1/files?folder_token=${encodeURIComponent(folder_token)}&page_size=${page_size}`)));

server.registerTool("feishu_get_document", {
  description: "读取飞书文档的纯文本内容。传入 document_id。",
  inputSchema: { document_id: z.string().min(1) }
}, async ({ document_id }) => text(await api(`/open-apis/docx/v1/documents/${encodeURIComponent(document_id)}/raw_content`)));

server.registerTool("feishu_create_document", {
  description: "在指定文件夹创建飞书文档。folder_token 可选；不传则创建在根目录。",
  inputSchema: { title: z.string().min(1), folder_token: z.string().optional() }
}, async ({ title, folder_token }) => text(await api("/open-apis/docx/v1/documents", { method: "POST", body: JSON.stringify({ title, folder_token }) })));

server.registerTool("feishu_append_text", {
  description: "向飞书文档末尾追加纯文本。",
  inputSchema: { document_id: z.string().min(1), content: z.string().min(1) }
}, async ({ document_id, content }) => text(await api(`/open-apis/docx/v1/documents/${encodeURIComponent(document_id)}/blocks`, { method: "POST", body: JSON.stringify({ children: [{ block_type: 2, text: { elements: [{ text_run: { content } }] } }] }) })));

await server.connect(new StdioServerTransport());
