---
name: mcp-manager
description: |
  配置、测试、刷新和使用本项目中的 MCP 服务器及工具。适用场景：
  (1) 用户要求添加、修改、启用、禁用或删除 MCP 服务器
  (2) 用户想测试 MCP 连接、查看工具、刷新工具缓存或启用/禁用某个工具
  (3) 用户想在 Agent/Flow 中使用 MCP 工具
  (4) 用户提供 stdio、SSE 或 streamable-http MCP 配置并需要判断是否可用
  (5) MCP 报 command not found、npx 不存在、连接失败、超时或工具不可见
  触发词：「添加 MCP」「配置 MCP」「MCP 服务器」「MCP 工具」「测试 MCP」「刷新 MCP」「npx 不存在」「command not found」
---

# MCP Manager

服务器：`http://127.0.0.1:8000`

## 核心规则

1. **先测试，后接入**：保存 MCP 配置后必须调用测试接口；测试失败时先处理错误，不要把未验证的服务器连接到 Agent。
2. **命令运行在后端机器**：stdio 的 `command`、依赖包和环境变量由运行 LangGraph 后端的机器提供，不是浏览器所在机器。
3. **不要擅自安装依赖**：命令不存在时告知缺失依赖和安装方式，等待用户确认后再执行安装。
4. **工具节点不是执行节点**：MCP 节点不加入 LangGraph 执行图，只通过 `tools` 边向 LLM 提供工具。
5. **敏感信息不回显**：不要在回复、日志或示例中输出 API Key、Authorization、Cookie 等真实值。

## 传输方式与配置

### stdio：本地命令

配置 `transport=stdio`，在 `config` 中填写：

```json
{
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:\\data"],
  "env": {"EXAMPLE_TOKEN": "从环境变量读取"},
  "timeout": 120
}
```

- `command`：可执行命令名或绝对路径。
- `args`：命令参数数组，不要把整条命令拼成一个字符串。
- `env`：仅填写 MCP 进程需要的环境变量。
- `timeout`：工具调用超时时间，1-600 秒；不确定时使用 120。
- Windows 路径用 JSON 双反斜杠，例如 `C:\\Users\\me\\data`。

### sse：SSE 服务

配置 `transport=sse`，填写 `config.url`，需要认证时填写 `config.headers`：

```json
{
  "url": "https://example.com/mcp/sse",
  "headers": {"Authorization": "Bearer <token>"},
  "timeout": 60
}
```

### streamable-http：HTTP MCP 服务

配置 `transport=streamable-http`，填写 MCP 服务 URL 和可选请求头：

```json
{
  "url": "https://example.com/mcp",
  "headers": {"X-API-Key": "<token>"},
  "timeout": 60
}
```

## 添加和验证 MCP

按以下顺序执行，不跳过测试：

1. 确认传输方式、服务器名称、启动命令或远程 URL，以及所需环境变量。
2. 对 stdio 命令先做本机可用性检查，检查位置必须是后端运行环境。
3. 调用创建接口保存配置：

```http
POST /api/mcp-server/create
Content-Type: application/json
```

```json
{
  "name": "filesystem",
  "description": "访问指定目录的文件工具",
  "transport": "stdio",
  "is_enabled": 1,
  "keep_alive": 1,
  "config": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:\\data"],
    "env": {},
    "timeout": 120
  }
}
```

4. 调用 `POST /api/mcp-server/test/{id}`，确认 `success=true` 并检查返回的 `tools`。
5. 测试成功后，可调用 `POST /api/mcp-server/refresh/{id}` 清除旧缓存并重新发现工具。
6. 测试失败时记录 `error`，修复配置或依赖后重试，不要只根据服务器名称判断成功。

## 判断 stdio 命令是否支持

按照当前操作系统检查命令是否存在，再测试版本或帮助信息。不要直接执行可能修改数据的命令。

### Windows

```powershell
Get-Command npx
npx --version
Get-Command uvx
uvx --version
```

### Linux/macOS

```bash
command -v npx
npx --version
command -v uvx
uvx --version
```

判断标准：

- 找不到命令：明确告知命令名、运行环境和安装依赖，停止继续测试。
- 命令存在但版本检查失败：告知 PATH、权限或运行时版本问题，停止继续测试。
- 命令检查成功：再调用 MCP 测试接口验证启动参数、包名、权限和协议握手。
- `npx` 缺失：提示安装 **Node.js（包含 npm/npx）**，安装后重新打开终端或重启后端，使 PATH 生效；不要只提示“安装 npx”。
- `node` 存在而 `npx` 缺失：提示修复/重装 npm，或使用 Node.js 官方安装包重新安装，并再次检查 `node --version`、`npm --version`、`npx --version`。
- `uvx` 缺失：提示安装 uv；不要把 Python 的 `uvx` 和 Node.js 的 `npx` 混淆。

推荐反馈格式：

```text
MCP 命令 npx 在后端运行环境中不可用，暂时无法测试连接。
请先安装 Node.js（Node.js 安装包会包含 npm 和 npx），安装后重启后端，再重试。
```

若命令是绝对路径，仍要检查文件存在、运行权限和工作目录。若命令依赖网络下载包，说明首次连接可能需要网络访问，并让用户确认后再测试。

## 在 Agent/Flow 中使用

1. 先在 MCP 管理页面创建并测试服务器。
2. 在 Flow/Agent 编辑器添加 `mcp` 节点。
3. 在 MCP 节点配置中选择一个或多个已启用、测试成功的服务器。
4. 从 MCP 节点的 `tools` 输出连接到 LLM 节点的 `tools` 输入；不要连接普通 `default` 边。
5. 保存并执行 Agent，观察 LLM 实际获得的工具名和工具调用结果。

工具名通常会加服务器前缀：`mcp__服务器名称__工具名称`，用于避免不同 MCP 服务器的同名工具冲突。

连接关系：

```text
[MCP 节点] --tools--> [LLM 节点]
```

MCP 节点本身不产生流程输出。未连接到 LLM、服务器 ID 为空、服务器禁用或测试失败时，LLM 不会获得对应工具。

## 工具管理

- 查看启用服务器：`GET /api/mcp-server/list`
- 查看详情和配置：`GET /api/mcp-server/get/{id}`
- 测试连接并发现工具：`POST /api/mcp-server/test/{id}`
- 清缓存并重新发现工具：`POST /api/mcp-server/refresh/{id}`
- 更新工具启用状态：

```http
PUT /api/mcp-server/tools/status
Content-Type: application/json
```

```json
{
  "server_id": 1,
  "tool_name": "list_directory",
  "is_enabled": 0
}
```

- 修改服务器配置后，系统会清理旧连接并尝试刷新；仍应手动检查测试结果。
- `keep_alive=1` 复用连接；`keep_alive=0` 每次调用后释放连接，适合资源占用高或不稳定的 MCP。

## 常见故障

| 现象 | 检查顺序 |
|---|---|
| `command not found` | 检查后端机器 PATH、命令安装状态和运行用户；`npx` 缺失安装 Node.js |
| stdio 启动后无工具 | 检查 `args` 是否拆分正确、包名/版本、工作目录和环境变量 |
| 连接超时 | 检查网络、远程 URL、代理、认证头和 `timeout`；不要无限增大超时 |
| 测试成功但 Agent 无工具 | 检查 MCP 节点服务器 ID、启用状态、`tools` 边和 LLM 是否为目标节点 |
| 工具名称冲突 | 使用带 `mcp__服务器名__` 前缀的实际工具名，刷新缓存 |
| 工具调用连续超时 | 检查服务端日志和工具输入；stdio 连续超时会自动断开并在下次调用重连 |

错误即停止当前操作，保留原始错误信息和配置字段名，修复后从“测试连接”步骤重新开始。
