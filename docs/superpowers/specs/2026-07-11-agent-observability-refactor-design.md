# IC Env Guard Agent 可扩展观测重构规格

**状态：** Draft for review

**日期：** 2026-07-11

**目标版本：** Agent API v2

**范围：** `agent` 运行模式

## 1. 摘要

本次重构将 IC Env Guard 收敛为一个可独立部署的 Linux Host Agent。Agent 保留现有浏览器登录和 PTY Shell 能力，同时提供本地数据写入 API、SQLite 最新状态存储、远端查询 API和 Prometheus 兼容导出。

监控逻辑不内置到 Agent。Shell、Python 程序、cron job、systemd timer 或其他本地工具负责采集数据，再把最新 Observation 或 Log Source 元数据提交给 Agent。Agent 负责输入校验、TTL、持久化、权限控制、查询和导出。

代码采用模块化单体：模块可以独立开发和测试，但产品仍以一个 Agent 安装包和一个主进程交付。本次不拆微服务，不引入必需的 PostgreSQL、消息队列、服务发现或 Kubernetes。

## 2. 背景与现状

当前仓库已经实现：

- FastAPI Agent 和 Control Plane 两种运行模式；
- bearer token 登录；
- 浏览器 PTY、WebSocket、回放和终端生命周期；
- 受配置约束的服务管理；
- 主机快照和 Prometheus 指标；
- SQLite 审计和状态迁移；
- React Web UI；
- 后端 contract、integration、unit 测试和前端 Vitest 测试。

当前主要结构问题是：

- `main.py` 同时负责配置解析、对象创建、数据库生命周期、后台任务、依赖覆盖和路由组合；
- Agent 与 Control Plane 的路由、依赖和状态在同一个应用工厂中按 mode 分支组合；
- Control Plane 中存在较多面向具体 Agent API 的转发代码；
- 监控能力以 Agent 内置采集器为主，增加新的业务检查通常需要修改 Agent；
- API、业务逻辑和 SQLite 模型之间的边界不够稳定；
- 前端同时承担 Fleet 和单主机工作区，增加了 Agent-only 部署的认知成本。

本规格不以“拆更多进程”为目标，而以“形成稳定模块接口，让采集逻辑在 Agent 外部扩展”为目标。

## 3. 目标

### 3.1 产品目标

1. 每台受管理 Linux Server 运行一个 Agent。
2. 用户可以通过远端 Web 登录 Agent，并创建真实 PTY Shell。
3. Shell 可以运行其操作系统账号有权运行的程序；是否可以使用 `sudo` 完全由主机 sudoers 策略决定。
4. 本地程序可以通过本地 HTTP API 上报最新 Observation。
5. 每个 Observation 必须具有独立 TTL，过期状态由 Agent 统一计算。
6. Observation 支持 `details` 字典，以承载无需索引和导出的扩展信息。
7. 本地程序可以注册 Log Source 元数据，但不把原始日志内容写入 SQLite。
8. 已授权远端用户可以按 Log ID 获取当前日志尾部内容。
9. 远端系统可以通过结构化 HTTP API 或 Prometheus `/metrics` 获取最新数据。
10. SQLite 只保存最新状态和有界的过期记录，不承担长期时序数据库职责。

### 3.2 工程目标

1. Auth、Terminal、Observations、Logs、Metrics、Audit、Storage 和 Bootstrap 模块具有明确边界。
2. 领域逻辑不依赖 FastAPI、SQLAlchemy 或 Prometheus Client 的具体实现。
3. 每个模块可以使用内存适配器独立执行单元测试。
4. HTTP、SQLite 和 Prometheus 通过端口/适配器连接到应用层。
5. `create_app()` 不再直接构造所有业务对象；组合集中在 Bootstrap Composition Root。
6. Agent v2 契约使用明确版本、统一错误格式和可重复的 contract tests。

## 4. 非目标

本次重构不包括：

- 将模块拆成独立微服务或容器；
- 实现中央 Control Plane、Fleet 调度或跨 Agent 聚合；
- 在 Agent 内实现通用调度器、告警引擎或规则引擎；
- 替代 cron、systemd timer、Prometheus、Alertmanager 或 Grafana；
- 在 SQLite 中保存长期指标历史；
- 将完整日志内容写入 SQLite 或 Prometheus；
- 允许远端 API 提交任意文件路径进行读取；
- 通过 Observation API 执行任意命令；
- 在第一版实现 PAM、LDAP、OIDC 或浏览器用户到多个 Linux 账号的动态映射；
- 删除现有 Control Plane 实现。Control Plane mode 在本次迁移期间冻结，不增加新功能，其后续保留、简化或移除需要单独规格。

## 5. 核心架构

```text
Remote Browser
  └── HTTPS ──> Agent Public HTTP
                  ├── Auth API
                  ├── Web UI
                  ├── Terminal HTTP/WebSocket API
                  ├── Observation Read API
                  ├── Log Read/Tail API
                  └── Prometheus /metrics

Local collectors
  └── localhost HTTP + producer token
          └── Agent Local Ingest API
                  ├── Observation Upsert
                  └── Log Source Upsert

Agent application modules
  ├── Auth
  ├── Terminal
  ├── Observations
  ├── Logs
  ├── Metrics
  ├── Audit
  ├── Storage ports
  └── Bootstrap composition root

Infrastructure adapters
  ├── SQLite
  ├── PTY / OS process
  ├── Local filesystem
  ├── FastAPI HTTP/WebSocket
  └── Prometheus exposition
```

### 5.1 部署模型

- 每台受管理 Server 安装一个 Agent。
- Agent 作为一个 systemd service 运行。
- Agent 主进程包含所有应用模块。
- Public HTTP 默认绑定 `127.0.0.1`；远端暴露必须显式开启并通过 HTTPS 或受信任的 TLS 反向代理。
- Local Ingest HTTP 单独绑定 `127.0.0.1`，不得配置为非 loopback 地址。
- Public HTTP 和 Local Ingest HTTP 可以由同一主进程启动两个监听器，但二者使用不同端口、认证策略和路由集合。
- 默认端口：Public `8765`，Local Ingest `8766`。

### 5.2 模块边界

建议目标包结构：

```text
backend/ic_env_guard/
  bootstrap/       配置加载、Composition Root、进程生命周期
  auth/            浏览器认证、producer 认证、权限上下文
  terminal/        PTY 会话、回放、ticket、WebSocket 应用服务
  observations/    Observation 模型、验证、TTL、upsert/query 用例
  logs/            Log Source 模型、路径策略、tail 用例
  metrics/         Observation/Log 到 Prometheus 的只读转换
  audit/           登录和高风险操作的安全审计
  storage/         Repository Protocol 和 SQLite 适配器
  api/             Public/Ingest HTTP 与 WebSocket 适配器
  config/          纯配置模型和校验
```

边界规则：

- 模块只能通过公开应用服务或 Protocol 接口调用其他模块。
- 领域模型不得导入 `fastapi`、`sqlalchemy`、`prometheus_client` 或前端类型。
- API 层不得直接执行 SQL。
- Metrics 模块只能读取 Observation 和 Log Source，不得修改它们。
- `details` 不得被 Auth、Metrics 或路由层解释为权限或配置。
- 不创建包含跨领域业务逻辑的通用 `utils` 模块。
- SQLite 可以使用一个数据库文件，但表所有权和 Repository 必须按模块隔离；不得跨 Repository join 后形成隐式耦合。

## 6. 身份认证与 Web Terminal

### 6.1 浏览器认证

- Agent v2 继续支持现有 bearer token 管理员登录，以降低迁移成本。
- token 文件必须是普通文件，权限不得允许非 owner 读取。
- Public HTTP 非 loopback 暴露时必须启用 HTTPS 或部署在受信任 TLS 反向代理之后。
- 登录失败必须限流并写入安全审计。
- token、Terminal ticket 和 producer token 不得出现在日志、审计、metrics、API 错误或前端持久化状态中。
- v2 将当前认证身份明确命名为 `local-admin`；多用户身份系统不在本规格范围内。

### 6.2 Shell 权限语义

- Terminal 创建的 Shell 以 Agent 配置的操作系统账号运行；默认是 Agent systemd service 的运行账号。
- Shell 继承该账号的 UID、GID、supplementary groups、环境策略和文件权限。
- Agent 不实现自定义命令白名单，也不拦截 Shell 内执行的命令。
- Agent 不自动提升权限、不注入 sudo 密码，也不修改 `/etc/sudoers`。
- 如果运行账号被系统管理员授予 sudo 权限，Shell 可以按标准 Linux sudo 规则使用该权限。
- 将 Agent 账号配置为 `NOPASSWD: ALL` 等价于向 Agent 管理员凭据授予远程 root Shell，部署文档必须显著提示该风险。

### 6.3 Terminal 行为

- 保留创建、列表、详情、resize、history、connect-token、WebSocket attach 和关闭能力。
- Terminal 输出继续使用有界内存回放缓冲，不默认持久化内容。
- SQLite 只保存 Terminal 元数据和生命周期审计。
- Terminal 必须执行 idle timeout、断开处理、退出检测和 orphan process 清理。
- 每个 connect-token 一次性使用、短期有效且绑定 Terminal 和认证主体。

## 7. Observation 领域模型

### 7.1 输入模型

```json
{
  "namespace": "eda",
  "name": "license_server_alive",
  "kind": "gauge",
  "value": 1,
  "unit": "boolean",
  "status": "ok",
  "message": "lmgrd is running",
  "labels": {
    "server": "license01",
    "vendor": "synopsys"
  },
  "details": {
    "pid": 1234,
    "port": 27000,
    "version": "11.19",
    "features": ["compiler", "verdi"]
  },
  "observed_at": "2026-07-11T10:00:00Z",
  "ttl_seconds": 120
}
```

### 7.2 字段规则

| 字段 | 要求 |
|---|---|
| `namespace` | 必填；正则 `^[a-z][a-z0-9_]{0,62}$` |
| `name` | 必填；正则 `^[a-z][a-z0-9_]{0,126}$` |
| `kind` | 必填；v2 支持 `gauge`、`counter`、`status` |
| `value` | `gauge`/`counter` 必须是有限数值；`status` 可以省略 |
| `unit` | 可选；最多 32 字符，不参与唯一标识 |
| `status` | 必填；`ok`、`warning`、`critical`、`unknown` |
| `message` | 可选；UTF-8，最多 2048 bytes |
| `labels` | 可选；最多 16 项；key 使用 namespace 规则；value 最多 128 bytes |
| `details` | 可选 JSON object；序列化后最多 16 KiB；最大嵌套深度 4 |
| `observed_at` | 必填 RFC 3339 UTC 时间；不得晚于 Agent 当前时间 60 秒以上 |
| `ttl_seconds` | 必填整数；范围 1 至 604800 秒 |

补充规则：

- `NaN`、正负 Infinity、二进制内容和非 JSON 类型必须拒绝。
- `details` key 最多 64 bytes，单个字符串值最多 4096 bytes。
- `details` 可以包含 object、array、string、number、boolean 和 null。
- `details` 不参与 Observation 唯一标识。
- `details` 不建立数据库查询索引。
- `details` 不自动导出到 Prometheus。
- Producer 不得在 `message`、`labels` 或 `details` 中提交 token、密码、私钥或完整敏感环境变量。

### 7.3 唯一标识和更新规则

- Observation 逻辑身份由 `namespace + name + canonical(labels)` 决定。
- Agent 对排序后的规范化 labels 计算稳定 `identity_key`。
- 相同身份的提交执行 upsert，只保留最新有效结果。
- `expires_at = observed_at + ttl_seconds`，由 Agent 计算。
- 如果提交到达时已经过期，返回 `422 observation_expired`。
- 如果提交记录的 `observed_at` 早于已有记录，返回 `409 stale_observation`，不得覆盖新值。
- 相同 `observed_at` 且规范化内容完全一致视为幂等重试，返回当前记录。
- 相同 `observed_at` 但内容不同返回 `409 observation_timestamp_conflict`。
- `received_at` 和 `updated_at` 由 Agent 生成。

### 7.4 过期语义

- `now < expires_at`：`fresh`。
- `now >= expires_at`：`stale`。
- 默认查询只返回 fresh 数据。
- 已授权查询可以使用 `include_stale=true` 获取宽限期内的 stale 数据。
- stale Observation 不再导出原始 value 到 Prometheus。
- stale 数据在 `expired_retention_seconds` 宽限期后物理删除。
- 默认 `expired_retention_seconds=86400`；允许范围 0 至 604800 秒。
- 清理任务默认每 60 秒运行一次，批量删除，不阻塞 API 请求。

## 8. Observation HTTP API v2

### 8.1 Local Ingest API

```text
PUT /api/v2/observations
```

- 仅在 Local Ingest listener 提供。
- 必须通过 producer bearer token 认证。
- 请求体是单个 Observation。
- 新建返回 `201`，更新或幂等重试返回 `200`。
- 响应返回规范化后的完整记录，包括 `identity_key`、`received_at`、`expires_at` 和 `stale`。

批量写入不属于 v2 初始范围。Producer 需要逐条 upsert；后续只有在实际吞吐量证明必要时才增加 batch API。

### 8.2 Public Read API

```text
GET /api/v2/observations
GET /api/v2/observations/{identity_key}
```

列表支持：

- `namespace` 精确过滤；
- `name` 精确过滤；
- `status` 精确过滤；
- `include_stale=false|true`；
- `limit`，默认 100，最大 1000；
- opaque cursor 分页。

Public Read API 需要管理员 bearer token。响应中的 `details` 原样返回；消费者必须忽略未知字段。

## 9. Log Source 领域模型

### 9.1 输入模型

```json
{
  "path": "/eda/logs/innovus/run.log",
  "last_updated": "2026-07-11T09:59:58Z",
  "observed_at": "2026-07-11T10:00:00Z",
  "ttl_seconds": 120
}
```

Log ID 位于 URL 中：

```text
PUT /api/v2/logs/innovus-run-log
```

### 9.2 字段规则

| 字段 | 要求 |
|---|---|
| `log_id` | URL path 参数；正则 `^[a-z][a-z0-9_.-]{0,126}$` |
| `path` | 必填绝对路径；规范化后必须位于 `logs.allowed_roots` |
| `last_updated` | 必填；采集程序观察到的文件 mtime，RFC 3339 UTC |
| `observed_at` | 必填；检查文件的时间，RFC 3339 UTC |
| `ttl_seconds` | 必填整数；范围 1 至 604800 秒 |

Agent 增加并返回：

- `received_at`；
- `expires_at`；
- `stale`；
- 规范化后的 `path`。

Log Source 使用与 Observation 相同的时序冲突、幂等和 TTL 规则。

### 9.3 路径安全

- `logs.allowed_roots` 只存在于 Agent 本地配置。
- Web、Public API 和 Producer API 都无权扩大 allowed roots。
- 注册时必须执行 `realpath` 并在解析符号链接后再次校验根目录。
- 目标必须是已存在的普通文件；目录、设备、FIFO 和 socket 必须拒绝。
- tail 时必须重新执行同样的 realpath、allowed root 和文件类型校验，防止注册后替换符号链接。
- path 不得作为 Prometheus label。
- 日志内容不得写入 SQLite、应用日志、安全审计或 Prometheus。

## 10. Log HTTP API v2

### 10.1 Local Ingest API

```text
PUT /api/v2/logs/{log_id}
```

- 仅在 Local Ingest listener 提供。
- 使用 producer bearer token。
- 新建返回 `201`，更新或幂等重试返回 `200`。

### 10.2 Public Read API

```text
GET /api/v2/logs
GET /api/v2/logs/{log_id}
GET /api/v2/logs/{log_id}/tail?lines=100
```

规则：

- 需要管理员 bearer token 和 `logs:read` 权限；v2 的单管理员模型默认拥有该权限。
- 列表默认只返回 fresh Log Source。
- `tail` 默认返回最后 100 行；`lines` 范围 1 至 1000。
- 单次响应最多读取和返回 1 MiB；达到字节限制时返回 `truncated=true`。
- 返回 UTF-8；无效字节使用 replacement character，不因单个非法字节失败。
- 不支持 follow、搜索、正则、下载完整文件或读取历史轮转文件。
- 未注册 ID 返回 `404 log_source_not_found`。
- stale ID 返回 `410 log_source_stale`。
- 文件消失返回 `410 log_file_unavailable`。
- 文件在注册后移出 allowed roots 返回 `403 log_path_forbidden`，并写入安全审计。

tail 响应示例：

```json
{
  "id": "innovus-run-log",
  "path": "/eda/logs/innovus/run.log",
  "lines": ["...", "..."],
  "line_count": 100,
  "truncated": true,
  "last_updated": "2026-07-11T09:59:58Z"
}
```

远端 API 不提供 `?path=` 参数；远端只能通过稳定 Log ID 读取已由本地 Producer 注册且满足本地安全策略的文件。

## 11. SQLite 数据模型

### 11.1 `observations`

```text
identity_key       TEXT PRIMARY KEY
namespace          TEXT NOT NULL
name               TEXT NOT NULL
kind               TEXT NOT NULL
numeric_value      REAL NULL
unit               TEXT NULL
status             TEXT NOT NULL
message            TEXT NULL
labels_json        TEXT NOT NULL
details_json       TEXT NOT NULL
observed_at        TEXT NOT NULL
received_at        TEXT NOT NULL
expires_at         TEXT NOT NULL
producer_id        TEXT NOT NULL
updated_at         TEXT NOT NULL
```

索引：

- `(namespace, name)`；
- `(status, expires_at)`；
- `expires_at`。

`labels_json` 使用排序 key 的紧凑 JSON；`details_json` 默认存储 `{}`。不为 JSON 内部字段建立索引。

### 11.2 `log_sources`

```text
id                 TEXT PRIMARY KEY
path               TEXT NOT NULL
last_updated       TEXT NOT NULL
observed_at        TEXT NOT NULL
received_at        TEXT NOT NULL
expires_at         TEXT NOT NULL
producer_id        TEXT NOT NULL
updated_at         TEXT NOT NULL
```

索引：

- `expires_at`；
- `last_updated`。

### 11.3 数据库运行规则

- 继续使用 migration-managed SQLite。
- 启用 foreign keys、busy timeout 和 WAL mode。
- 每次 upsert 使用单个短事务。
- tail 日志文件时不得持有数据库事务。
- 数据库异常返回稳定错误，不在响应中暴露 SQL、文件路径或内部堆栈。
- Observation、Log Source、Terminal metadata 和 Audit 使用独立 Repository。

## 12. Prometheus 导出

Agent 继续提供：

```text
GET /metrics
```

### 12.1 Observation 映射

fresh Observation 导出：

```text
ic_env_observation_value{
  namespace="eda",
  name="license_server_alive",
  server="license01",
  vendor="synopsys"
} 1

ic_env_observation_status{
  namespace="eda",
  name="license_server_alive",
  status="ok"
} 1
```

规则：

- `gauge` 和 `counter` 的数值导出为 `ic_env_observation_value`。
- `status` 导出为 one-hot `ic_env_observation_status`。
- stale Observation 不导出 value/status 样本，使 Prometheus 按正常 scrape 语义将旧 series 标记 stale。
- `message`、`unit` 和 `details` 不成为 labels。
- Producer labels 必须先通过数量、key、value 和总序列数限制。
- Agent 配置 `metrics.max_observation_series` 默认 10000；达到上限时拒绝创建新 identity，但允许更新已有 identity。

### 12.2 Log Source 映射

```text
ic_env_log_source_last_updated_seconds{log_id="innovus-run-log"} 1783781998
ic_env_log_source_stale{log_id="innovus-run-log"} 0
```

- 只使用有界 `log_id` label。
- path 和日志内容不得导出。
- 宽限期内的 stale Log Source 可以导出 `stale=1`，但不得导出日志内容。

### 12.3 `/metrics` 访问

- 默认只允许 loopback。
- 远端 Prometheus scrape 必须显式配置 CIDR allowlist，或由受信任反向代理执行 mTLS。
- `/metrics` 不使用浏览器 bearer token。
- scrape 过程只读，不更新 Observation 的 TTL。

## 13. Local Producer 模型

### 13.1 Producer 认证

- Local Ingest API 只监听 loopback。
- Producer 使用独立 token 文件，不复用浏览器管理员 token。
- token 文件权限必须为 owner-only readable。
- 每个请求解析出稳定 `producer_id`，写入 Observation 或 Log Source。
- 初始版本支持一个本机 producer token；多 producer token 和细粒度 namespace 权限为后续扩展。

### 13.2 调度责任

Agent 不调度采集程序。推荐：

- 长期固定检查使用 systemd timer；
- 简单兼容场景可以使用 cron；
- EDA 程序在运行结束或状态变化时主动提交；
- Producer 自己决定采集周期，TTL 应大于正常周期并预留失败宽限。

例如每 60 秒采集一次的检查，建议 TTL 设为 120 至 180 秒。

## 14. 配置规格

新增配置示例：

```yaml
mode: agent

server:
  bind: 127.0.0.1
  port: 8765
  remote_bind_enabled: false

ingest:
  bind: 127.0.0.1
  port: 8766
  token_file: /etc/ic-env-guard/producer-token
  max_request_bytes: 32768

observations:
  expired_retention_seconds: 86400
  cleanup_interval_seconds: 60

logs:
  allowed_roots:
    - /eda/logs
    - /var/log/eda
  max_tail_lines: 1000
  default_tail_lines: 100
  max_tail_bytes: 1048576

metrics:
  enabled: true
  max_observation_series: 10000
  remote_network_allowlist: []
```

配置校验必须保证：

- `mode` 是 `agent` 时才启动 Local Ingest listener；
- `ingest.bind` 必须是 loopback 地址；
- Public 和 Ingest 端口不得相同；
- producer token 与管理员 token 不得相同；
- token 文件权限符合安全要求；
- `allowed_roots` 必须是绝对路径，启动时执行规范化和去重；
- 所有大小、数量、TTL 和间隔配置在本文规定范围内；
- 非 loopback Public bind 继续遵循现有 fail-closed 规则。

## 15. 统一错误模型

所有 v2 JSON 错误使用：

```json
{
  "error": {
    "code": "stale_observation",
    "message": "the submitted observation is older than the stored value",
    "correlation_id": "..."
  }
}
```

要求：

- `code` 稳定、机器可读；
- `message` 不包含 secret、SQL、内部路径或堆栈；
- 每个响应返回 `X-Correlation-ID`；
- 客户端提供 correlation ID 时必须验证长度和字符集；
- 认证失败使用 `401`，权限失败使用 `403`；
- schema/范围错误使用 `422`；
- 时序冲突使用 `409`；
- 数据库不可用使用 `503 storage_unavailable`；
- 未预期异常使用 `500 internal_error`，完整堆栈只写入受保护的服务日志。

## 16. 安全审计

安全审计继续记录：

- 登录成功和失败；
- Terminal 创建、attach、close、timeout 和异常退出；
- Log tail 请求，包括 actor、log ID、行数、结果和 source address；
- Log 路径安全拒绝；
- Observation/Log 写入认证失败；
- 配置加载和安全校验失败；
- 数据库迁移和存储健康变化。

安全审计不记录：

- Terminal 输入或输出；
- 日志内容；
- Observation `details` 全文；
- token 或认证 header；
- Producer 提交的任意敏感文本。

Observation 正常 upsert 不逐条写入安全审计，避免高吞吐和敏感数据风险；Agent metrics 记录成功、拒绝和失败计数。

## 17. 前端范围

Agent Web UI v2 必须：

- 支持管理员 token 登录和登出；
- 默认进入本机 Terminal 工作区；
- 保留多 Terminal tab、resize、reconnect、history 和 close；
- 不要求用户先选择 Fleet 或 Agent；
- 正确显示认证过期、Terminal 断开和 Agent 错误；
- 不把 token 写入 URL、日志或长期持久化。

Observation 和 Log Source 的新 Web 页面不属于初始重构的必须项。v2 首先交付稳定 HTTP/Prometheus 契约；现有 Metrics 页面可以在后续独立功能规格中改为消费 Observation API。

## 18. 兼容性与迁移

### 18.1 API 版本

- 新能力只在 `/api/v2` 提供。
- 现有 Terminal v1 HTTP/WebSocket 契约在至少一个发布周期内继续工作。
- v2 Terminal 可以先复用现有 v1 wire contract；内部模块化不得改变外部行为。
- 新 Observation 和 Log API 没有 v1 兼容负担。
- 废弃接口必须通过文档和响应 header 给出明确移除版本，不静默删除。

### 18.2 数据库迁移

- 新 migration 创建 `observations` 和 `log_sources`，不得修改或删除现有审计、Terminal、服务状态表。
- migration 必须可在现有 Agent SQLite 上原地运行。
- upgrade 前安装流程继续备份数据库。
- migration 失败必须 fail clearly，Agent 不以部分 schema 启动。
- rollback 到不识别新表的旧版本时允许新表保留；旧版本不得读取或修改它们。

### 18.3 分阶段迁移

1. 固化现有 Agent contract tests 和 Terminal 行为基线。
2. 引入 Composition Root 和模块接口，保持行为不变。
3. 创建 Observation/Log 领域模型、Repository 和 SQLite migration。
4. 增加 Local Ingest API 和 Public Read API。
5. 增加 Prometheus 动态导出和 series 限制。
6. 将 Agent Web UI 默认路由从 Fleet 工作区简化到本机 Terminal。
7. 更新安装、配置、安全和 Producer 示例文档。
8. 在一个完整发布周期后，根据使用情况单独决定 v1 和 Control Plane 的未来。

每个阶段必须可独立测试和发布，不允许长期维护一个不可运行的大重写分支。

## 19. 测试策略

### 19.1 Unit tests

- Observation 字段和 `details` 大小/深度校验；
- identity key 的 label canonicalization；
- TTL、fresh/stale 和 cleanup 判定；
- 乱序、幂等和 timestamp conflict；
- Log realpath、allowed roots 和 symlink escape；
- tail 行数、字节数、UTF-8 replacement 和 truncation；
- Prometheus name/label 验证和 series 上限；
- secret redaction；
- 配置 fail-closed 校验。

### 19.2 Contract tests

- Local Ingest Observation create/update/error 响应；
- Public Observation list/detail/filter/pagination；
- Log Source upsert/list/detail/tail；
- 统一 v2 error envelope 和 correlation ID；
- `/metrics` fresh/stale 行为；
- v1 Terminal HTTP 和 WebSocket 回归契约。

### 19.3 Integration tests

- SQLite migration、restart 后数据保留和 WAL 并发；
- Producer 写入、Public API 查询和 Prometheus scrape 完整链路；
- 到期后 Public API、tail 和 Prometheus 的一致行为；
- 文件注册后被删除、替换为 symlink 或移出 allowed root；
- 1000 行/1 MiB tail 边界；
- PTY 创建、输入输出、resize、reconnect、close 和 orphan cleanup；
- Public 与 Ingest listener 的网络隔离；
- 管理员 token 和 producer token 权限隔离；
- Agent restart 后 Terminal 状态 reconciliation 和 Observation 保留。

### 19.4 Frontend tests

- 登录后直接进入本机 Terminal；
- Terminal tab 和 reconnect 行为；
- 认证过期清理；
- 不再依赖 Fleet selection 才能打开 Agent Terminal；
- v1 API 兼容期间的路由行为。

### 19.5 Security tests

- 任意路径、`..`、symlink race 和特殊文件读取被拒绝；
- 非 loopback Ingest bind 被配置校验拒绝；
- token 不出现在错误、日志、审计和 metrics；
- 超大 `details`、深层 JSON、过多 labels 和超大请求被拒绝；
- Prometheus label cardinality 上限生效；
- 未认证用户不能读取 Observation、Log 或 Terminal；
- Producer token 不能调用 Public 管理和 Terminal API。

## 20. 验收标准

满足以下条件时，本次重构可以验收：

1. 现有 Agent Terminal contract 和 integration tests 全部通过。
2. 登录后无需 Fleet/Agent 选择即可创建本机 PTY。
3. 一个本地 Producer 可以写入包含 `details` 的 Observation，Agent restart 后仍可查询。
4. 相同 Observation 的旧结果不能覆盖新结果，幂等重试不会创建重复记录。
5. 每个 Observation 按自身 TTL 转为 stale，并在宽限期后清理。
6. stale Observation 不再作为有效值出现在 Prometheus scrape 中。
7. Log Source 只保存元数据，SQLite 中不存在原始日志内容。
8. 已授权用户可以通过 Log ID 获取最后 100 行，不能通过 API 读取 allowed roots 外文件。
9. `details` 最多 16 KiB，可经 SQLite 完整 round-trip，但不出现在 Prometheus labels 中。
10. Public 和 Ingest listener 使用不同端口和不同 token；Producer token 不能创建 Terminal。
11. 所有 v2 错误符合统一 envelope，并包含 correlation ID。
12. Auth、Terminal、Observations、Logs、Metrics、Audit 和 Storage 可以分别使用内存依赖运行单元测试。
13. 完整后端测试、frontend test/build 和 lint 全部通过。
14. 安装、升级和 rollback 文档覆盖新增端口、token、数据库表和配置。

## 21. 风险与缓解

### 21.1 Web Shell 等价于远程主机权限

风险：管理员 token 泄露可能导致远程执行任意命令；运行账号具有 sudo 时影响可达到 root。

缓解：HTTPS、严格 token 文件权限、限流、短期 Terminal ticket、安全审计、最小 sudoers 和默认 loopback bind。部署文档必须明确风险，不把 bearer token 描述为低风险只读凭据。

### 21.2 任意 `details` 导致存储膨胀或泄密

缓解：16 KiB 限制、深度限制、请求体限制、过期清理、禁止自动导出、Producer 指南和 secret redaction 测试。

### 21.3 Log tail 形成任意文件读取

缓解：本地注册、稳定 Log ID、allowed roots、realpath 双重校验、普通文件限制、tail 限制和安全审计。

### 21.4 动态 labels 导致 Prometheus 高基数

缓解：label 数量/长度限制、series 总上限、拒绝新 identity、禁止 path/message/details 成为 label。

### 21.5 SQLite 写入竞争

缓解：最新值模型、短事务、WAL、busy timeout、批量后台清理以及不在数据库事务中读取日志文件。初始版本不增加 batch ingest；只有实测证明需要时才扩展。

## 22. 已确认设计决策

1. 采用模块化单体，不采用微服务。
2. 每台受管理 Server 运行一个 Agent。
3. 浏览器直接登录目标 Agent；中央 Control Plane 不属于本次核心链路。
4. Agent 提供真实 PTY，权限由 Agent 运行账号和 Linux sudoers 决定。
5. 采集调度放在 Agent 外部，由本地程序、cron 或 systemd timer 完成。
6. Agent 接收并保留最新 Observation，不保存长期时序历史。
7. Observation 具有独立 TTL 和可扩展 `details` JSON object。
8. Log 只保存 `{path, last_updated, observed_at, ttl_seconds}` 及 Agent 生成的元数据。
9. 远端通过稳定 Log ID 按需读取有界 tail，不能传入任意 path。
10. Log allowed roots 只由 Agent 本地配置控制，远端无权扩大。
11. Prometheus 负责远端数值采集和长期时序历史。
12. 原始日志内容不进入 SQLite、审计或 Prometheus。
