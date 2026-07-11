# IC Env Guard Agent 与 Fleet Console 可扩展观测及前端重构规格

**状态：** Draft for review

**日期：** 2026-07-11

**目标版本：** Agent API v2 / Manager API v2

**范围：** `agent` 与 `control-plane`（产品界面称为 Manager）运行模式

## 1. 摘要

本次重构保留一个代码库和一个安装包，但明确两种可组合的运行方式：每台 Linux Server 上运行可独立使用的 Agent；需要集中管理时，额外运行一个轻量 Manager。Agent 保留浏览器登录和 PTY Shell，并提供本地数据写入 API、SQLite 最新状态存储、远端查询 API 和 Prometheus 兼容导出。Manager 提供 Agent 注册、删除、验证、状态探测、受控网络发现、API/Terminal 代理和统一 Fleet Web Console。

监控逻辑不内置到 Agent。Shell、Python 程序、cron job、systemd timer 或其他本地工具负责采集数据，再把最新 Observation 或 Log Source 元数据提交给 Agent。Agent 负责输入校验、TTL、持久化、权限控制、查询和导出。

代码采用模块化单体：模块可以独立开发和测试，但每种运行模式仍是一个主进程。Manager 不是新的微服务集群，也不复制 Agent 的长期监控数据；Prometheus 继续负责时间序列历史。浏览器在 Manager 模式下只连接 Manager，不持有 Agent token，也不直接扫描网络或连接 Agent。

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
- `AppRoutes.tsx` 使用组件本地 state 模拟 Fleet/Host/Page 路由，页面不可深链接，返回操作和状态恢复不稳定；
- `AgentContext.tsx` 同时承担 Fleet 拉取、失败回退、Agent 选择、启停和缓存更新，形成单一高耦合全局状态；
- Agent 注册表完全来自启动 YAML，运行时只能 list、probe 和 enable/disable，不能通过 Web/API 添加、编辑或删除；
- Fleet 卡片适合少量 Agent，但缺少地址、验证状态、服务/Observation 汇总和可扩展的表格操作；
- Agent selector、一级导航和 Host 页签处于相近层级，用户难以判断当前是在 Fleet、某台主机还是某项功能；
- Audit 等页面缺少统一表格、过滤、加载、空状态和错误恢复体验。

本规格不以“拆更多服务”为目标，而以稳定的 Agent/Manager 契约、可替换模块边界和清晰的 Fleet 信息架构解决扩展问题。

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
11. Agent 必须能够不依赖 Manager 独立运行、登录和使用 Terminal。
12. Manager 用户可以按 Agent 地址添加、验证、编辑、启停和删除 Agent。
13. Manager 必须列出所有 Agent，并显示地址、连接状态、版本、能力、最后探测时间、Observation 状态和受管服务摘要。
14. Manager 可以在本地配置允许的网络范围和端口集合内执行受控 Agent 发现。
15. 发现结果必须经过凭据验证才能加入注册表，扫描成功不等于信任或注册成功。
16. Manager 用户可以从 Agent 详情进入 Terminal、Services、Observations、Logs、Metrics 和 Audit，不需要重新输入 Agent token。
17. Manager 不保存 Observation/Prometheus 长期历史，只缓存 Fleet 页面所需的最新状态摘要。

### 3.2 工程目标

1. Auth、Terminal、Observations、Logs、Metrics、Audit、Storage 和 Bootstrap 模块具有明确边界。
2. 领域逻辑不依赖 FastAPI、SQLAlchemy 或 Prometheus Client 的具体实现。
3. 每个模块可以使用内存适配器独立执行单元测试。
4. HTTP、SQLite 和 Prometheus 通过端口/适配器连接到应用层。
5. `create_app()` 不再直接构造所有业务对象；组合集中在 Bootstrap Composition Root。
6. Agent v2 契约使用明确版本、统一错误格式和可重复的 contract tests。
7. Agent Registry、Discovery、Availability 和 Agent Proxy 具有独立应用服务和 Repository 接口。
8. React 前端按 feature 组织，路由状态进入 URL，远端数据状态由 query cache 管理。
9. Terminal 状态按 `agent_id + terminal_id` 隔离，切换页面或 Agent 时不得串线。
10. Agent-only 和 Manager UI 复用同一套 feature，但通过 Runtime Capabilities 决定入口和可见导航。

## 4. 非目标

本次重构不包括：

- 将模块拆成独立微服务或容器；
- 将 Manager 拆成多个微服务或引入分布式服务发现；
- 让浏览器直接访问 Agent token、Agent 内网地址或执行端口扫描；
- 扫描用户任意输入的 CIDR、端口范围或公网；
- 发现后自动信任、自动写入凭据或自动安装 Agent；
- 在 Manager 中复制 Prometheus 时间序列或建立第二套监控历史库；
- 在 Agent 内实现通用调度器、告警引擎或规则引擎；
- 替代 cron、systemd timer、Prometheus、Alertmanager 或 Grafana；
- 在 SQLite 中保存长期指标历史；
- 将完整日志内容写入 SQLite 或 Prometheus；
- 允许远端 API 提交任意文件路径进行读取；
- 通过 Observation API 执行任意命令；
- 在第一版实现 PAM、LDAP、OIDC 或浏览器用户到多个 Linux 账号的动态映射；
- 在第一版实现批量共享 Agent token、自动证书签发或 Agent 主动注册；这些能力只有在单 Agent 凭据流程稳定后才能单独设计。

## 5. 核心架构

```text
Standalone usage
  Remote Browser ──HTTPS──> Agent Public HTTP / Web UI

Fleet usage
  Remote Browser ──HTTPS──> Manager Public HTTP / Fleet Web UI
                                  │
                                  ├── Agent Registry + latest status cache
                                  ├── bounded Discovery jobs
                                  ├── Agent API proxy
                                  └── Terminal WebSocket proxy
                                            │
                                            └──HTTPS──> Agent(s)

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

Manager application modules
  ├── Agent Registry
  ├── Credential Store
  ├── Availability Probe
  ├── Discovery
  ├── Fleet Summary
  ├── Agent HTTP/WebSocket Proxy
  └── Control-plane Audit
```

### 5.1 部署模型

- 每台受管理 Server 安装一个 Agent，并作为一个 systemd service 运行。
- Agent 主进程包含所有 Agent 应用模块，可以单独提供 Web UI、Terminal、API 和 `/metrics`。
- 集中管理部署额外运行一个 Manager；现有配置值继续使用 `mode: control-plane`，产品文案统一显示 “Manager”。
- Manager 是一个主进程和一个 SQLite 数据库，不要求 PostgreSQL、Broker 或 Kubernetes。
- 同一进程实例不能同时以 Agent 和 Manager 身份运行；本地开发由 `start.sh all` 启动两个进程。
- Public HTTP 默认绑定 `127.0.0.1`；远端暴露必须显式开启并通过 HTTPS 或受信任的 TLS 反向代理。
- Local Ingest HTTP 单独绑定 `127.0.0.1`，不得配置为非 loopback 地址。
- Public HTTP 和 Local Ingest HTTP 可以由同一主进程启动两个监听器，但二者使用不同端口、认证策略和路由集合。
- 默认端口：Public `8765`，Local Ingest `8766`。
- Manager 只暴露自己的浏览器会话；Agent 凭据始终保留在 Manager 服务端。

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
  fleet/           Manager Registry、Availability、Summary 用例
  discovery/       受控扫描 scope、job 和 candidate 验证
  proxy/           Agent HTTP/Terminal WebSocket 代理
```

边界规则：

- 模块只能通过公开应用服务或 Protocol 接口调用其他模块。
- 领域模型不得导入 `fastapi`、`sqlalchemy`、`prometheus_client` 或前端类型。
- API 层不得直接执行 SQL。
- Metrics 模块只能读取 Observation 和 Log Source，不得修改它们。
- `details` 不得被 Auth、Metrics 或路由层解释为权限或配置。
- 不创建包含跨领域业务逻辑的通用 `utils` 模块。
- SQLite 可以使用一个数据库文件，但表所有权和 Repository 必须按模块隔离；不得跨 Repository join 后形成隐式耦合。
- Manager Registry 不得再以启动 YAML 中的 `agents` 列表作为唯一事实来源；YAML 只用于首次导入和本地安全边界。
- Discovery 只产生未受信任 Candidate，不得直接调用 Registry 写入。
- Agent Proxy 只接受 Registry 中已启用且已验证的 Agent ID，不接受用户提供的任意 upstream URL。

## 6. 身份认证与 Web Terminal

### 6.1 浏览器认证

- Agent v2 继续支持现有 bearer token 管理员登录，以降低迁移成本。
- Manager 使用独立管理员 token；Manager 登录凭据和任何 Agent token 不得相同。
- 浏览器登录 Agent 时只能管理该主机；浏览器登录 Manager 时通过 Manager 访问整个 Fleet。
- token 文件必须是普通文件，权限不得允许非 owner 读取。
- Public HTTP 非 loopback 暴露时必须启用 HTTPS 或部署在受信任 TLS 反向代理之后。
- 登录失败必须限流并写入安全审计。
- token、Terminal ticket 和 producer token 不得出现在日志、审计、metrics、API 错误或前端持久化状态中。
- v2 将当前认证身份明确命名为 `local-admin`；多用户身份系统不在本规格范围内。

### 6.2 Manager 到 Agent 的身份

- 添加 Agent 时，管理员在 HTTPS 表单中提供 Agent base URL 和 Agent token。
- `validate` API 只能在内存中使用该 token 完成一次连接验证，不得持久化、记录或返回 token。
- `create/update` API 必须重新验证同一组连接参数，验证成功后才写 Registry。
- Manager 将每个 Agent token 原子写入 `credential_directory` 下使用随机 opaque 文件名的独立文件。
- credential 文件必须由 Manager 运行账号拥有、权限为 `0600`；SQLite 只保存 credential reference，不保存 token 明文。
- 删除 Agent 时必须删除对应 credential 文件；审计记录保留，但不得包含 token。
- Manager 代理请求时从 Credential Store 读取 token，并通过 server-to-server `Authorization` header 发送；浏览器永远看不到该 header。
- 非 loopback Agent 默认必须使用 HTTPS 和验证通过的证书。开发模式允许显式配置的 loopback HTTP，不允许 UI 临时绕过 TLS 校验。
- Credential Store 写入必须使用同目录临时文件、`fsync`、`chmod` 和 atomic rename；数据库提交失败时删除新文件，启动时清理无 Registry reference 的 orphan。
- credential directory 必须是 Manager owner 的真实目录而非 symlink；credential 必须是 Manager owner 的普通文件，不得接受 symlink、device 或 group/other readable 文件。

### 6.3 Shell 权限语义

- Terminal 创建的 Shell 以 Agent 配置的操作系统账号运行；默认是 Agent systemd service 的运行账号。
- Shell 继承该账号的 UID、GID、supplementary groups、环境策略和文件权限。
- Agent 不实现自定义命令白名单，也不拦截 Shell 内执行的命令。
- Agent 不自动提升权限、不注入 sudo 密码，也不修改 `/etc/sudoers`。
- 如果运行账号被系统管理员授予 sudo 权限，Shell 可以按标准 Linux sudo 规则使用该权限。
- 将 Agent 账号配置为 `NOPASSWD: ALL` 等价于向 Agent 管理员凭据授予远程 root Shell，部署文档必须显著提示该风险。

### 6.4 Terminal 行为

- 保留创建、列表、详情、resize、history、connect-token、WebSocket attach 和关闭能力。
- Terminal 输出继续使用有界内存回放缓冲，不默认持久化内容。
- SQLite 只保存 Terminal 元数据和生命周期审计。
- Terminal 必须执行 idle timeout、断开处理、退出检测和 orphan process 清理。
- 每个 connect-token 一次性使用、短期有效且绑定 Terminal 和认证主体。
- Manager Terminal ticket 还必须绑定 `agent_id`；Manager WebSocket attach 先获取本地 proxy slot，再换取上游 Agent ticket。

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
- 单次最多读取 960 KiB 日志内容，整个 JSON wire response 不得超过 1 MiB；达到任一限制时返回 `truncated=true`。
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

## 11. Manager Agent Registry

### 11.1 Agent 身份与状态

Agent 安装时生成稳定 UUID `instance_id` 并写入 `/var/lib/ic-env-guard/instance-id`；升级已有 Agent 时只生成一次，后续重启、升级和地址变化均保持不变。已认证的 `/api/v2/capabilities` 返回：

```json
{
  "instance_id": "a670d8f8-6074-4d7e-a118-15f445a25d72",
  "name": "EDA Host 01",
  "api_version": "2",
  "agent_version": "0.3.0",
  "capabilities": [
    "terminals.v1",
    "services.v1",
    "observations.v2",
    "logs.v2",
    "summary.v2"
  ]
}
```

`instance_id` 使用标准小写 UUID。Agent identity file 不是 secret，但必须只允许管理员修改；文件缺失或格式错误时 Agent 启动失败并给出恢复指引，不能每次启动生成新 ID。

Manager Registry 保存：

- Manager 路由使用的稳定 `agent_id`；
- Agent 自报且全局唯一的 `instance_id`；
- 管理员可修改的 `display_name`；
- 规范化 endpoint；
- `credential_ref`；
- 本地 TLS profile reference；
- enabled 状态；
- 创建/更新时间；
- 最近一次验证的版本和 capabilities。

`agent_id` 是 Manager 内不可变的路由 key：YAML import 保留现有配置 ID 以兼容旧 URL；Web 新增时默认使用 Agent `instance_id`。`instance_id` 单独存储并用于跨地址去重，用户只能修改 display name，不能修改这两个身份字段。

Manager 把连接状态和 Agent 内工作负载健康分开：

```text
connection_status:
  disabled
  unknown -> ready
          -> degraded
          -> unavailable
  ready/degraded/unavailable -> unknown（状态超过 stale_after）

workload_status:
  unknown | healthy | warning | critical | stale
```

- `ready`：认证、API version 和必需 capabilities 均正确。
- `degraded`：Agent 可连接，但缺少非核心 capability 或协议兼容性降级；Observation warning/critical 不改变 connection status。
- `unavailable`：DNS、TCP、TLS、认证、协议或超时失败。
- `unknown`：尚未验证或最近状态已过期。
- `disabled`：管理员关闭路由和自动 probe。
- validate/probe in-flight 是 UI/应用操作状态，不持久化为 connection status。
- workload status 只从最新 Agent Summary 计算；summary 过期为 `stale`，尚无 summary 为 `unknown`。
- UI 必须显示状态文字和图标，不能只依赖颜色。

### 11.2 Registry API v2

```text
POST   /api/v2/agents/validate
POST   /api/v2/agents
GET    /api/v2/agents
GET    /api/v2/agents/{agent_id}
PUT    /api/v2/agents/{agent_id}
DELETE /api/v2/agents/{agent_id}
POST   /api/v2/agents/{agent_id}/probe
POST   /api/v2/agents/{agent_id}/enabled
GET    /api/v2/fleet/overview
```

Validate/Create 请求使用相同 connection input；Create 额外接受 display name：

```json
{
  "base_url": "https://eda-host-01.example:8765",
  "display_name": "EDA Host 01",
  "token": "write-only-agent-token",
  "tls_profile_id": "eda-internal-ca"
}
```

- `token` 是 write-only；任何响应、列表或 detail 均不得回显。
- `tls_profile_id` 默认 `system`，使用操作系统 trust store；其他值必须引用 Manager 本地配置中已存在的 profile。API 不接受 CA 文件路径或关闭验证的布尔值。
- List 支持 `query`、`connection_status`、`workload_status`、`capability`、`limit`（默认 100，最大 1000）和 opaque cursor。
- Create 返回 `201`，Update/Enable/Probe 返回 `200`，Delete 返回 `204`。

添加流程：

1. Web 表单在本地完成 URL shape 校验。
2. `POST /agents/validate` 使用未持久化 token 请求目标 `/api/v2/capabilities`，身份和协议验证成功后再尝试 `/api/v2/summary`。
3. Manager 返回 Agent ID、name、版本、证书摘要、capabilities 和 summary 预览，不返回 token。
4. 任一输入发生变化后，前端必须使旧验证结果失效。
5. 用户确认后，`POST /agents` 重新验证并在一个应用事务中写 credential 文件和 Registry。
6. Agent `instance_id` 或规范化 endpoint 已存在时返回 `409 agent_already_registered`。

`validate` 响应必须分别报告 `network`、`tls`、`authentication`、`protocol`、`identity`、`capabilities` 和 `readiness` 阶段，便于 UI 给出可恢复错误；任一字段只允许稳定状态和安全 message，不返回原始 socket exception。Network/TLS/auth/protocol/identity 是添加 gate；Summary/readiness 失败作为 warning，不阻止身份和协议正确的 Agent 注册。Validate 不写 Registry、credential file 或长期数据库。

更新规则：

- 修改 display name 或 enabled 不要求重新提交 token。
- 修改 base URL、token 或 TLS profile 必须重新验证，成功后原子替换并增加 Registry revision。
- 失败更新不得破坏原有可用配置。
- `DELETE` 的 UI 文案为“Remove from Manager”，不得暗示会卸载远端 Agent 或删除 Agent 本地数据。
- Agent 存在活跃 Terminal proxy 时删除返回 `409 agent_in_use`；用户先关闭 Terminal 后重试。
- 删除成功后 Registry 和 credential file 被移除；Control-plane Audit 保留。
- list/detail API 必须返回 enabled、disabled、unknown、ready、degraded 和 unavailable Agent，不得因为 Agent 离线而隐藏。
- Offline/disabled Agent 仍可读取 Registry Overview/Settings、修改凭据、重新验证和删除；只有需要 upstream 的 Terminal/Service/Log 操作被禁用。
- 任何 Registry 响应不得包含 token、credential path 或 Authorization header。
- Probe 开始时读取 Registry revision；完成写 status 时 revision 必须仍匹配，避免旧地址的迟到 probe 覆盖新配置状态。
- 动态 URL 校验、probe 和实际代理必须共用 `AgentTargetPolicy`：DNS 全部解析结果位于 allowed CIDR，禁止 self target/metadata/link-local/multicast/unspecified/reserved、禁止 redirect，并把连接 pin 到已验证 IP，同时保留原 hostname 用于 SNI/Host，防止 DNS rebinding。
- `config_import` 且 endpoint 未改变的旧 Agent 继续按原启动配置安全规则运行；一旦通过 Web 修改 endpoint，该记录转为动态策略并必须满足 `allowed_agent_cidrs`。此兼容例外不得用于新增 Agent。

### 11.3 Agent Summary

Agent 增加只读：

```text
GET /api/v2/summary
```

返回 bounded summary：

```json
{
  "observed_at": "2026-07-11T10:00:00Z",
  "observations": {
    "total": 42,
    "warning": 2,
    "critical": 1,
    "stale": 3
  },
  "logs": {
    "total": 8,
    "stale": 1
  },
  "services": {
    "total": 6,
    "running": 5,
    "unhealthy": 1
  },
  "terminals": {
    "active": 2
  }
}
```

- Summary 只包含计数和状态，不包含 Observation details、日志内容或 Terminal 内容。
- `workload_status` 优先级为：Summary 过期 `stale`；无 Summary `unknown`；任一 critical Observation 或 unhealthy Service 为 `critical`；否则任一 warning/stale Observation 或 stale Log Source 为 `warning`；其余为 `healthy`。
- Manager 定期 probe 时把每个 Agent 的最新 Summary 缓存在 Manager SQLite；每个 Agent 只保留一条最新记录。
- Fleet 页面从缓存 summary 渲染，Agent 详情页按需代理实时 API。
- 单个 Agent 失败不得使 Fleet Overview 整体失败；响应必须包含该 Agent 的 last known status 和稳定错误类别。

`GET /api/v2/fleet/overview` 返回 `collected_at` 和 `agents[]`。每项包含 Registry 的 `agent_id`、`instance_id`、`display_name`、安全可展示 endpoint、enabled、connection/workload status，以及 `agent_status` 中的 observed/stale time、version、capabilities、summary 和 `last_error_code`；不包含 credential reference。排序默认按 connection severity、workload severity、display name，前端可再按 URL query 调整。

## 12. 受控 Agent Discovery

### 12.1 安全模型

浏览器不得使用 Web API、CORS 绕过或客户端脚本直接扫描网络。扫描由 Manager 后端执行，并受本地配置中的命名 Scope 约束：

```yaml
control_plane:
  discovery:
    scopes:
      - id: eda-lab
        name: EDA lab network
        cidr: 10.20.30.0/24
        ports: [8765, 9443]
        schemes: [https]
```

约束：

- UI 只能选择预配置 Scope，不能提交任意 CIDR、任意端口范围或 URL。
- `scopes: []` 是默认值，此时 Discovery API 返回 feature disabled；管理员必须在本地配置显式启用。
- 单个 Scope 最多 256 个地址、8 个端口；更大网络必须由管理员拆分。
- 默认最大并发连接 32，单地址连接超时 500 ms，HTTP fingerprint 超时 2 秒，整个 job 最长 120 秒。
- Manager 只执行 TCP connect 和 IC Env Guard fingerprint 请求，不进行通用 banner grabbing。
- 非私有地址、Manager 自身地址、multicast、unspecified、reserved 和 link-local 地址必须拒绝。
- Agent 的公开 `/healthz` 响应增加 `X-IC-Env-Guard-Agent: 2` header；发现只确认产品 fingerprint，不返回 Agent token、Terminal 或敏感配置。
- 发现 Candidate 是不可信输入；必须走正常 token/TLS validation 才能加入 Registry。

### 12.2 Discovery API

```text
GET    /api/v2/discovery/scopes
POST   /api/v2/discovery/jobs
GET    /api/v2/discovery/jobs/{job_id}
POST   /api/v2/discovery/jobs/{job_id}/cancel
GET    /api/v2/discovery/jobs/{job_id}/results
```

Job 状态：`queued`、`running`、`completed`、`cancelled`、`failed`。

结果字段：

- candidate URL；
- IP、port 和 scheme；
- fingerprint API version；
- 首次/最近发现时间；
- `new` 或 `already_registered`；
- 验证状态；
- 有界错误类别。

Discovery job 和结果保留 24 小时后清理。UI 每秒 polling job 状态即可；初版不增加 SSE/WebSocket。批量自动添加不在范围内，每个 Candidate 单独进入 Add Agent 验证流程。

## 13. Manager 路由与聚合

- 浏览器在 Manager 模式下只请求同源 `/api/v2/agents/{agent_id}/...` 路由。
- Manager 从 path 中解析 Agent ID，再从 Registry 获取 base URL 和 credential；请求体不得包含 upstream URL。
- v2 明确允许的详情代理至少包括：

```text
GET /api/v2/agents/{agent_id}/services
GET /api/v2/agents/{agent_id}/observations
GET /api/v2/agents/{agent_id}/observations/{identity_key}
GET /api/v2/agents/{agent_id}/logs
GET /api/v2/agents/{agent_id}/logs/{log_id}
GET /api/v2/agents/{agent_id}/logs/{log_id}/tail
GET /api/v2/agents/{agent_id}/audit
```

- Service mutation 和 Terminal 继续复用现有显式 Agent-scoped 路由及 v1 wire contract；禁止实现接受任意 method/path 的通用 proxy endpoint。
- Observation、Log、Service 和 Audit 详情采用按需代理，不复制到 Manager SQLite。
- Fleet Overview 使用最近 Summary cache，自动 probe 默认每 15 秒运行一次，并加入 bounded jitter。
- “Monitoring” 全局页面只使用 Registry 中缓存的每 Agent Summary，按 workload status 和问题计数列出 Agent；具体 Observation、Service 和 Log 明细必须进入单个 Agent 后按需加载。初版不实现跨 Agent 明细 fan-out。
- Prometheus 推荐直接 scrape 每个 Agent；初版 Manager 不重新导出所有 Agent metrics，也不实现 Prometheus service-discovery endpoint。
- Terminal WebSocket 必须继续使用一次性 Manager ticket、上游一次性 Agent ticket、容量限制、frame 限制、backpressure 和 correlation ID。

## 14. SQLite 数据模型

### 14.1 `observations`

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

### 14.2 `log_sources`

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

### 14.3 Manager `agents`

```text
agent_id            TEXT PRIMARY KEY
instance_id         TEXT NULL UNIQUE
display_name        TEXT NOT NULL
normalized_endpoint TEXT NOT NULL UNIQUE
credential_ref      TEXT NOT NULL
tls_profile_id      TEXT NOT NULL
enabled             INTEGER NOT NULL
source              TEXT NOT NULL
revision            INTEGER NOT NULL
created_at          TEXT NOT NULL
updated_at          TEXT NOT NULL
```

`source` 是 `config_import`、`manual` 或 `discovery`。Version、capabilities 和 summary 不属于配置表。

Manager 每 Agent 一行的 `agent_status`：

```text
agent_id             TEXT PRIMARY KEY REFERENCES agents(agent_id)
target_revision      INTEGER NOT NULL
connection_status    TEXT NOT NULL
workload_status      TEXT NOT NULL
observed_at          TEXT NULL
stale_after          TEXT NULL
api_version          TEXT NULL
agent_version        TEXT NULL
capabilities_json    TEXT NOT NULL
summary_json         TEXT NOT NULL
last_error_code      TEXT NULL
updated_at           TEXT NOT NULL
```

另有有界的 `discovery_jobs`、`discovery_results`。这些表属于 Fleet/Discovery Repository，不与 Agent 本地 `observations` 或 `log_sources` 混用。

### 14.4 数据库运行规则

- 继续使用 migration-managed SQLite。
- 启用 foreign keys、busy timeout 和 WAL mode。
- 每次 upsert 使用单个短事务。
- tail 日志文件时不得持有数据库事务。
- 数据库异常返回稳定错误，不在响应中暴露 SQL、文件路径或内部堆栈。
- Observation、Log Source、Terminal metadata 和 Audit 使用独立 Repository。

## 15. Prometheus 导出

Agent 继续提供：

```text
GET /metrics
```

### 15.1 Observation 映射

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

### 15.2 Log Source 映射

```text
ic_env_log_source_last_updated_seconds{log_id="innovus-run-log"} 1783781998
ic_env_log_source_stale{log_id="innovus-run-log"} 0
```

- 只使用有界 `log_id` label。
- path 和日志内容不得导出。
- 宽限期内的 stale Log Source 可以导出 `stale=1`，但不得导出日志内容。

### 15.3 `/metrics` 访问

- 默认只允许 loopback。
- 远端 Prometheus scrape 必须显式配置 CIDR allowlist，或由受信任反向代理执行 mTLS。
- `/metrics` 不使用浏览器 bearer token。
- scrape 过程只读，不更新 Observation 的 TTL。

## 16. Local Producer 模型

### 16.1 Producer 认证

- Local Ingest API 只监听 loopback。
- Producer 使用独立 token 文件，不复用浏览器管理员 token。
- token 文件权限必须为 owner-only readable。
- 每个请求解析出稳定 `producer_id`，写入 Observation 或 Log Source。
- 初始版本支持一个本机 producer token；多 producer token 和细粒度 namespace 权限为后续扩展。

### 16.2 调度责任

Agent 不调度采集程序。推荐：

- 长期固定检查使用 systemd timer；
- 简单兼容场景可以使用 cron；
- EDA 程序在运行结束或状态变化时主动提交；
- Producer 自己决定采集周期，TTL 应大于正常周期并预留失败宽限。

例如每 60 秒采集一次的检查，建议 TTL 设为 120 至 180 秒。

## 17. 配置规格

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
  max_tail_bytes: 983040

metrics:
  enabled: true
  max_observation_series: 10000
  remote_network_allowlist: []
```

Manager 示例：

```yaml
mode: control-plane

server:
  bind: 127.0.0.1
  port: 8765
  remote_bind_enabled: false

control_plane:
  audit_database: /var/lib/ic-env-guard/control-plane.db
  credential_directory: /var/lib/ic-env-guard/agent-credentials
  poll_interval_seconds: 15
  status_stale_after_seconds: 45
  max_parallel_probes: 8
  max_active_terminal_proxies: 64
  max_outstanding_tickets: 128
  allowed_agent_cidrs:
    - 10.20.30.0/24
  tls_profiles:
    - id: eda-internal-ca
      ca_bundle: /etc/ic-env-guard/eda-internal-ca.pem
  discovery:
    max_concurrency: 32
    connect_timeout_milliseconds: 500
    fingerprint_timeout_seconds: 2
    job_timeout_seconds: 120
    result_retention_seconds: 86400
    scopes:
      - id: eda-lab
        name: EDA lab network
        cidr: 10.20.30.0/24
        ports: [8765, 9443]
        schemes: [https]
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
- `control-plane` mode 不启动 PTY、Local Ingest 或 Agent 本地数据采集模块；
- Registry、latest status、Discovery 和 Control-plane Audit 共用现有 `control_plane.audit_database` SQLite 文件，但使用各自 Repository/table；不新增第二个 Manager database path；
- `credential_directory` 必须由 Manager 账号拥有且目录权限不宽于 `0700`；
- TLS profile ID 必须唯一；CA bundle 是本地普通文件且启动时验证，路径和值不通过 Public API 暴露；
- `system` 是保留 TLS profile ID，不得在配置中覆盖；
- `allowed_agent_cidrs` 默认空列表；为空时现有导入 Agent 可继续工作，但 Web Add/Edit endpoint 和 Discovery 明确 disabled，UI 显示本地配置指引；
- 动态添加和 Discovery 只能访问 `allowed_agent_cidrs`；DNS 解析结果也必须落在允许范围内；
- Discovery Scope CIDR 必须是 `allowed_agent_cidrs` 的子网，地址数不得超过 256；
- Discovery ports 必须显式列出，单 Scope 不超过 8 个；
- 非 loopback HTTP Agent 即使位于允许 CIDR 也必须拒绝，不能由 UI 覆盖。

## 18. 统一错误模型

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

Manager 至少定义：

- `agent_not_found`、`agent_disabled`、`agent_in_use`；
- `agent_already_registered`、`agent_identity_mismatch`；
- `agent_network_error`、`agent_tls_error`、`agent_auth_error`、`agent_protocol_error`、`agent_version_unsupported`；
- `agent_validation_required`、`agent_validation_changed`；
- `discovery_disabled`、`discovery_scope_forbidden`、`discovery_job_not_found`、`discovery_capacity_exceeded`。

Frontend 只根据 `code` 决定交互分支；`message` 用于安全显示，correlation ID 提供复制按钮以便排障。

## 19. 安全审计

安全审计继续记录：

- 登录成功和失败；
- Terminal 创建、attach、close、timeout 和异常退出；
- Log tail 请求，包括 actor、log ID、行数、结果和 source address；
- Log 路径安全拒绝；
- Observation/Log 写入认证失败；
- 配置加载和安全校验失败；
- 数据库迁移和存储健康变化。
- Agent validate/add/edit/enable/disable/remove/probe；
- Discovery start/cancel/finish，包括 scope、候选数量和结果，不记录逐地址原始错误；
- Manager Agent-scoped proxy intent/outcome 和 indeterminate mutation；
- Credential Store 写入、替换或删除失败的稳定类别。

安全审计不记录：

- Terminal 输入或输出；
- 日志内容；
- Observation `details` 全文；
- token 或认证 header；
- Producer 提交的任意敏感文本。

Observation 正常 upsert 不逐条写入安全审计，避免高吞吐和敏感数据风险；Agent metrics 记录成功、拒绝和失败计数。

Manager 的 validate 失败、Registry mutation、Discovery start/cancel 和 routed privileged request 必须复用现有 durable intent/outcome 模型。Audit intent 提交失败时 fail closed，不得写 Registry、credential、启动扫描或 dispatch upstream；outcome 提交失败不得把已成功的远端 mutation 伪装成失败重试。

## 20. 前端产品与工程设计

### 20.1 Runtime 与入口

前端启动后先请求：

```text
GET /api/v2/runtime
```

响应包含 `mode: agent|manager` 和当前实例 capabilities。前端使用同一构建产物，但按 mode 选择默认入口：

```json
{
  "mode": "manager",
  "capabilities": ["fleet.v2", "agent-registry.v2", "discovery.v2"]
}
```

Runtime endpoint 是登录前可读的低风险元数据，只返回 mode 和 capability IDs，不返回地址、Agent、版本细节或配置；响应使用 `Cache-Control: no-store`，失败时登录页显示可重试错误而不是猜测 mode。

- Agent：登录后进入 `/terminal`，同时提供本机 Services、Observations、Logs、Metrics 和 Audit。
- Manager：登录后进入 `/fleet`，同时提供 Fleet Monitoring 和 Control-plane Audit。
- 路由和 capability 决定页面是否可访问；不得通过散落在组件中的 mode 判断复制两套 UI。
- 用户直接访问无 capability 的 URL 时显示解释和返回入口，不渲染空白页。

### 20.2 Manager 信息架构

桌面端使用稳定的左侧一级导航：

```text
Fleet
Monitoring
Audit
```

- Header 只显示产品名、Manager 健康、当前用户和登出，不再放第二套同级导航。
- Fleet 是 Agent 注册表和日常入口。
- Monitoring 使用缓存 Summary 聚合各 Agent 的问题数量和 workload status，并链接到 Agent 明细；不复制或跨 Agent 拉取每条 Observation。
- Audit 展示 Manager 路由/注册/发现审计，并可按 Agent 下钻。
- Agent 详情通过 breadcrumb 和二级页签呈现，不使用全局 `<select>` 代替导航。
- 所有核心页面使用真实 URL，可刷新、收藏、前进/后退并恢复筛选条件。

路由：

```text
/login
/fleet
/agents/new
/discovery
/monitoring
/audit
/agents/:agentId/overview
/agents/:agentId/terminal
/agents/:agentId/services
/agents/:agentId/observations
/agents/:agentId/logs
/agents/:agentId/metrics
/agents/:agentId/audit
/agents/:agentId/settings
```

### 20.3 Fleet 页面

Fleet 页面采用 data-dense table + drill-down，而不是每 Agent 一个大卡片。页面组成：

1. 标题和一句当前范围说明；
2. Primary action “Add agent”；secondary actions “Discover agents” 和 “Refresh all”；
3. Ready、Degraded、Unavailable、Disabled 数量摘要；
4. 搜索、状态、capability 和问题级别过滤；
5. Agent table。

表格列：

- connection status 图标和文字；
- workload status 图标和文字；
- display name / agent ID；
- base URL；
- Agent/API version；
- Observation critical/warning/stale 数量；
- Services running/total/unhealthy；
- 最后探测时间；
- actions menu。

交互规则：

- 点击非 action 区域进入 Agent Overview。
- 行级常用 action 只保留 “Open” 和 “Probe”；Enable/Disable、Edit、Remove 放入 actions menu。
- 搜索使用 deferred value；筛选和排序写入 URL query。
- 表头 sticky，状态排序稳定；超过 50 行时才评估 virtualization，不预先引入。
- 768px 以下切换为紧凑 list cards，只保留状态、名称、问题摘要和 Open；不把 8 列表格强塞到窄屏。
- 空 Fleet 显示 “Add your first agent” 和 “Discover on a configured network” 两个明确入口。
- 部分 Agent 失败只影响对应行，页面继续显示 last known summary。

### 20.4 Add/Edit Agent 流程

Add Agent 使用两步页面，不使用一个包含所有高级字段的大弹窗：

**Step 1 — Connection**

- Base URL；
- display name（可选，验证后默认使用 Agent name）；
- Agent token，使用 password input 并支持临时 show/hide；
- TLS CA 选择只显示本地已配置选项，不能关闭非 loopback TLS verification；
- “Test connection” 是该步唯一 primary action。

**Step 2 — Verify & save**

- 显示 Agent ID、name、URL、证书摘要、version、capabilities 和当前 summary；
- 清楚显示 missing capability、重复 ID/URL 或版本不兼容；
- “Add agent” 只有验证成功时可用；
- 修改 Step 1 任一字段必须使旧验证失效。

表单要求：

- 所有字段有永久 label 和 helper text；
- URL 在 blur 时校验 shape，服务端错误显示在对应字段或 validation summary；
- 提交中按钮 disabled 并显示进度；错误必须说明原因和恢复操作；
- token 只保存在当前 React form state，离开页面立即清除，不进入 URL、sessionStorage、query cache、日志或 error telemetry；
- Edit 对 display name/enable 使用简单表单；改变 URL/credential 时重新进入验证流程；
- Remove 使用明确确认对话框，说明“只从 Manager 删除，不卸载远端 Agent”，并处理 `agent_in_use` 恢复路径。

### 20.5 Discovery 流程

Discovery 是独立页面：

1. 选择 Manager 配置提供的命名 Network Scope；
2. 显示将扫描的 CIDR、ports、地址数量和预计上限；
3. 用户显式启动，页面显示 progress、已检查/总数、发现数和 Cancel；
4. 结果表区分 New、Already registered 和 Validation required；
5. 选择一个 New Candidate 后跳转 Add Agent，并只预填 candidate URL；token 仍由用户输入。

- 页面关闭后 job 继续由 Manager 执行，返回同一 URL 可以恢复状态。
- Job 失败显示稳定错误和 Retry，不把底层 socket 错误/堆栈直接展示给用户。
- 初版不提供 “Select all and trust”，避免共享凭据和误注册。

### 20.6 Agent 详情与 Monitoring

Agent 详情 Header 固定显示：connection status、workload status、display name、base URL、last seen 和 Probe。二级页签为 Overview、Terminal、Services、Observations、Logs、Metrics、Audit、Settings；缺少 capability 的页签保持可见但 disabled，并解释原因。

- Overview 显示当前问题优先的摘要，不重复堆叠装饰性 KPI。
- Terminal 保留多 tab、resize、reconnect 和隐藏时不销毁会话；状态按 Agent 隔离。
- Services 使用可排序表格展示 state、health、allowed operations 和最近结果；只显示/启用 Agent 声明允许的 Start、Stop、Restart，mutation 不自动重试并保留独立 pending/result。
- Observations 使用 table 展示 namespace/name、status、value、observed/expiry；`details` 通过可展开 JSON detail 显示，不塞进表格列。
- Logs 展示 Log ID、path、last updated、stale；tail viewer 默认 100 行并明确 truncation。
- Audit 使用统一 DataTable、分页、时间/operation/result 过滤，不再渲染无边界的原生文本表格。
- 全局 Monitoring 默认只显示 Summary 中 warning、critical、stale 和 unhealthy 的 Agent；用户可以切换为 All，避免健康数据淹没问题。
- Summary 过期或 Agent 离线时显示 last-known counts 和明确 stale 标记；用户从该行进入 Agent Overview 或执行 Probe。

### 20.7 视觉系统与可访问性

本项目采用“Data-dense + Drill-down + Minimal Enterprise Tool”方向：

- Inter/system sans 用于 UI，Terminal 保持 monospace；字体使用系统栈或随安装包自托管，不依赖运行时访问 Google Fonts/CDN；
- 中性 surface 和清晰边框，单一低饱和蓝色作为交互主色；
- green/amber/red 只表达状态，状态同时包含 icon 和文字；
- 不使用荧光 terminal green 作为全局品牌色，不使用大面积渐变、glassmorphism、夸张标题或装饰动画；
- 使用 4/8 px spacing scale、44–48 px clickable table rows、44 px 最小交互目标；
- 组件使用 semantic tokens，禁止页面内散落 raw hex；light/dark theme 共用语义 token；
- focus ring 可见，完整键盘导航，route change 后 focus 移到主标题；
- loading 超过 300 ms 显示 skeleton/progress；错误使用 `role=alert`，状态更新使用 `aria-live=polite`；
- 动画只用于 150–250 ms 的状态/布局反馈，并遵循 `prefers-reduced-motion`；
- z-index 只使用 semantic scale `base=0`、`sticky=10`、`dropdown=20`、`dialog=40`、`toast=50`，禁止任意 `9999`；
- WCAG 2.2 AA：普通文本对比度至少 4.5:1，非文本状态标记至少 3:1，不能只用颜色表达含义。

Light theme baseline tokens：

| Role | Value |
|---|---|
| Canvas | `#f6f8fb` |
| Surface | `#ffffff` |
| Primary text | `#111827` |
| Muted text | `#4b5563` |
| Border | `#d1d5db` |
| Primary action | `#1d4ed8` |
| Focus ring | `#2563eb` |

状态色使用独立 surface + foreground token；不得直接把 red/green 文本放在无对比控制的页面背景上。Dark theme 在实现阶段按相同语义 token 提供，经独立对比度测试后才能启用。

### 20.8 前端代码边界

目标结构：

```text
frontend/src/
  app/
    App.tsx
    router.tsx
    RuntimeProvider.tsx
    shell/AppShell.tsx
  features/
    auth/
    fleet/
    agent-registry/
    discovery/
    terminal/
    services/
    observations/
    logs/
    metrics/
    audit/
  shared/
    api/client.ts
    components/
    styles/tokens.css
    styles/base.css
    testing/
```

规则：

- `app/router.tsx` 是路由唯一事实来源，删除 `AppRoutes` 中的 view/page state machine。
- 每个 feature 拥有自己的 API functions、response types、query keys、components 和 tests。
- `shared/api/client.ts` 只负责 base URL、认证、correlation ID、JSON/error envelope 和 abort，不包含业务 endpoint。
- 删除承担全部 Fleet 状态的 `AgentContext`；active Agent 来自 route param，server state 来自 feature query hooks。
- 本地 UI state 保留在最近共同组件，不引入 Redux 等全局 store。
- 使用 `react-router-dom` 管理 URL，`@tanstack/react-query` 管理 fetch/cache/polling/invalidation，`lucide-react` 提供一致 SVG icon。
- `Agent` domain type 只在 agent-registry feature 定义一次；Fleet row/detail 在其上组合 summary，删除当前重复的 `AgentSummary`/`FleetHost` 模型。
- 表单使用受控 React input 和 feature-local validation；初版不增加大型 form framework。
- `package.json` 不再使用 `latest`；依赖使用明确 semver range 并由 lockfile 固定。
- ESLint 必须通过 `typescript-eslint` 和 `eslint-plugin-react-hooks` 覆盖 `.ts/.tsx`；当前只 lint JS/JSX 的配置不得保留。
- 初版不引入 Redux、MUI、Ant Design、Tailwind 或另一套 component framework。
- query polling 在标签页隐藏时暂停；Terminal WebSocket 不进入 query cache。
- 所有异步 effect 支持 AbortSignal；切换 Agent 后旧响应不得写入新 Agent 页面。

### 20.9 Agent-only UI

Agent Web UI 复用 Terminal、Services、Observations、Logs、Metrics 和 Audit features，但不加载 Fleet、Registry 或 Discovery bundle。登录后默认进入本机 Terminal；顶部明确显示 “Standalone Agent” 和本机 agent ID，不显示无意义的 Agent selector。

## 21. 兼容性与迁移

### 21.1 API 版本

- 新能力只在 `/api/v2` 提供。
- 现有 `/api/agents`、`/api/fleet/overview` 和 Agent-scoped v1 proxy 在兼容期继续工作，但底层改为读取 SQLite Registry。
- 现有 Terminal v1 HTTP/WebSocket 契约在至少一个发布周期内继续工作。
- v2 Terminal 可以先复用现有 v1 wire contract；内部模块化不得改变外部行为。
- 新 Observation 和 Log API 没有 v1 兼容负担。
- 废弃接口必须通过文档和响应 header 给出明确移除版本，不静默删除。

### 21.2 数据库迁移

- 新 migration 创建 `observations` 和 `log_sources`，不得修改或删除现有审计、Terminal、服务状态表。
- Manager migration 创建 `agents`、`agent_status`、`discovery_jobs` 和 `discovery_results`。
- migration 必须可在现有 Agent SQLite 上原地运行。
- upgrade 前安装流程继续备份数据库。
- migration 失败必须 fail clearly，Agent 不以部分 schema 启动。
- rollback 到不识别新表的旧版本时允许新表保留；旧版本不得读取或修改它们。
- Manager 首次以新 schema 启动且 `agents` 表为空时，把现有 YAML `agents` 逐条验证并导入 SQLite；全部成功才提交。
- 导入成功后 SQLite Registry 成为唯一运行时事实来源；YAML `agents` 标记 deprecated，不再在每次启动覆盖 Web 变更。
- 导入时 credential 文件复制到 Manager credential directory；源 token 文件不自动删除。
- 回滚旧版本不会卸载 Agent，但旧版只能看到原 YAML 中的 Agent；动态添加的 Agent 必须在回滚说明中手工导出/恢复。

### 21.3 分阶段迁移

1. 固化现有 Agent、Control Plane、Terminal 和 Agent-scoped proxy contract tests。
2. 引入 Composition Root 和模块接口，保持现有 API 行为。
3. 创建 Observation/Log 领域模型、Repository 和 Agent SQLite migration。
4. 增加 Local Ingest API、Public Read API、Summary 和 Prometheus 动态导出。
5. 创建 SQLite Agent Registry/Credential Store，迁移 YAML registry，并让 v1 API 读取新 Registry。
6. 增加 Manager v2 validate/add/edit/remove/probe API。
7. 增加 bounded Discovery jobs 和安全测试。
8. 引入前端 Router、Runtime Capabilities、Query Client 和 App Shell，先保持旧页面可用。
9. 迁移 Fleet/Add/Discovery/Agent Detail，再逐项迁移 Terminal、Services、Observations、Logs、Metrics 和 Audit。
10. 删除 `AppRoutes` view state machine、全局 `AgentContext` 和已经无调用的旧 CSS/API wrapper。
11. 更新安装、配置、凭据备份、Discovery、安全、Producer 和回滚文档。

每个阶段必须可独立测试和发布，不允许长期维护一个不可运行的大重写分支。

### 21.4 实施计划拆分

本系统规格定义共同目标，但后续必须写两个独立 implementation plan：

- **Workstream A — Agent foundation：** Composition Root、Observation、Log、SQLite、Summary、Prometheus、Standalone UI 和 PTY 回归。
- **Workstream B — Fleet Console：** SQLite Registry、Credential Store、Validation、Discovery、Manager proxy、Fleet/Monitoring UI 和前端 feature architecture。

Workstream A 可独立交付 Agent v2；Workstream B 依赖 Agent v2 capabilities/summary 契约，但不得反向让 Agent 依赖 Manager。

## 22. 测试策略

### 22.1 Unit tests

- Observation 字段和 `details` 大小/深度校验；
- identity key 的 label canonicalization；
- TTL、fresh/stale 和 cleanup 判定；
- 乱序、幂等和 timestamp conflict；
- Log realpath、allowed roots 和 symlink escape；
- tail 行数、字节数、UTF-8 replacement 和 truncation；
- Prometheus name/label 验证和 series 上限；
- Registry URL normalization、duplicate ID/URL 和原子更新；
- Agent instance ID 首次生成、持久化、格式错误和升级保留；
- Credential Store 权限、原子替换和删除；
- Discovery Scope 子网、host/port 上限、job 状态和 cleanup；
- Fleet partial summary 和 status transition；
- secret redaction；
- 配置 fail-closed 校验。

### 22.2 Contract tests

- Local Ingest Observation create/update/error 响应；
- Public Observation list/detail/filter/pagination；
- Log Source upsert/list/detail/tail；
- 统一 v2 error envelope 和 correlation ID；
- `/metrics` fresh/stale 行为；
- Agent validate/create/update/delete/probe/list；
- Fleet Overview 和 `/api/v2/summary`；
- `/api/v2/capabilities` 返回稳定 `instance_id` 和 v1/v2 capability IDs；
- Discovery scopes/jobs/cancel/results；
- Manager Agent-scoped Observation/Log/Service/Terminal proxy；
- v1 Terminal HTTP 和 WebSocket 回归契约。

### 22.3 Integration tests

- SQLite migration、restart 后数据保留和 WAL 并发；
- Producer 写入、Public API 查询和 Prometheus scrape 完整链路；
- 到期后 Public API、tail 和 Prometheus 的一致行为；
- 文件注册后被删除、替换为 symlink 或移出 allowed root；
- 1000 行、960 KiB 内容和 1 MiB wire response tail 边界；
- PTY 创建、输入输出、resize、reconnect、close 和 orphan cleanup；
- Public 与 Ingest listener 的网络隔离；
- 管理员 token 和 producer token 权限隔离；
- Agent restart 后 Terminal 状态 reconciliation 和 Observation 保留；
- YAML Agent 首次导入、Manager restart 后 Registry/status 恢复；
- Agent credential 写入权限、更新失败 rollback 和删除；
- 多 Agent probe concurrency、单 Agent 超时和 Fleet partial result；
- Discovery job progress/cancel/timeout/dedupe/24 小时 cleanup；
- Add Agent 从 validate 到 save 的完整链路；
- Manager Terminal ticket 到 Agent Terminal ticket/WebSocket 的完整链路。

### 22.4 Frontend tests

- Runtime mode 正确选择 Agent Terminal 或 Manager Fleet 默认入口；
- URL deep link、back/forward、刷新和 query filter 恢复；
- Fleet 搜索/筛选/排序、partial error、empty state 和 responsive card fallback；
- Add Agent 验证成功/失败、输入变更使验证失效、token 不持久化；
- Discovery progress/cancel/results 和 candidate 到 Add Agent 的预填；
- Agent 删除确认、`agent_in_use` 恢复、enable/disable/probe；
- Capability 缺失页签 disabled reason；
- Terminal tab 和 reconnect 行为；
- 认证过期清理；
- 切换 Agent 后旧请求响应不污染新 Agent 页面；
- 键盘导航、focus restore、`role=alert`/`aria-live` 和 reduced motion；
- 375、768、1024 和 1440 px 关键布局；
- v1 API 兼容期间的路由行为。

### 22.5 Security tests

- 任意路径、`..`、symlink race 和特殊文件读取被拒绝；
- 非 loopback Ingest bind 被配置校验拒绝；
- token 不出现在错误、日志、审计和 metrics；
- 超大 `details`、深层 JSON、过多 labels 和超大请求被拒绝；
- Prometheus label cardinality 上限生效；
- 未认证用户不能读取 Observation、Log 或 Terminal；
- Producer token 不能调用 Public 管理和 Terminal API；
- 浏览器响应、query cache 和 WebSocket URL 不包含 Agent token；
- 动态 Agent URL 和 DNS 解析结果必须位于 allowed CIDR，redirect 和 DNS rebinding 被拒绝；
- Discovery 不能超出配置 Scope、host/port/concurrency/time 限制；
- Candidate fingerprint 不能绕过 token/TLS validation；
- credential directory/file 权限不合格时 Manager fail closed；
- 删除、编辑、Discovery 和 Agent validation 写入审计但不记录 secret。

## 23. 验收标准

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
15. Standalone Agent 登录后直接进入本机 Terminal，且不加载/显示 Fleet selector。
16. Manager 登录后 `/fleet` 能列出 SQLite Registry 中全部 Agent，包括 URL、status、version、Observation 和 Service summary。
17. 用户可以通过 Web 完成 validate → preview → add；无效 token、错误 API version 或重复 ID/URL 不得写入 Registry。
18. Agent instance ID 在重启和升级后保持不变；重复 instance ID 或 normalized endpoint 均不能注册。
19. 修改 URL/credential 必须重新验证；失败更新保留旧配置和连接能力。
20. 删除 Agent 会移除 managed credential file 但不影响远端 Agent 数据；活跃 Terminal 时删除被安全阻止。
21. Discovery 只能扫描命名 Scope，显示可取消进度，并把 Candidate 带入逐个验证流程。
22. 浏览器网络请求、storage、URL 和渲染内容中均不存在 Agent token。
23. Fleet 中单个 Agent 离线时其余 Agent 和 last-known summaries 仍正常显示。
24. `/agents/:agentId/...` 可深链接、刷新和前进/后退；Terminal/请求状态不跨 Agent 泄漏。
25. Fleet、Add、Discovery、Agent Detail 和 Audit 满足 WCAG 2.2 AA 关键交互要求，并通过 375–1440 px 布局验证。
26. `AppRoutes` view state machine 和承担 Fleet 全局状态的 `AgentContext` 被目标路由/query 架构替代。

## 24. 风险与缓解

### 24.1 Web Shell 等价于远程主机权限

风险：管理员 token 泄露可能导致远程执行任意命令；运行账号具有 sudo 时影响可达到 root。

缓解：HTTPS、严格 token 文件权限、限流、短期 Terminal ticket、安全审计、最小 sudoers 和默认 loopback bind。部署文档必须明确风险，不把 bearer token 描述为低风险只读凭据。

### 24.2 任意 `details` 导致存储膨胀或泄密

缓解：16 KiB 限制、深度限制、请求体限制、过期清理、禁止自动导出、Producer 指南和 secret redaction 测试。

### 24.3 Log tail 形成任意文件读取

缓解：本地注册、稳定 Log ID、allowed roots、realpath 双重校验、普通文件限制、tail 限制和安全审计。

### 24.4 动态 labels 导致 Prometheus 高基数

缓解：label 数量/长度限制、series 总上限、拒绝新 identity、禁止 path/message/details 成为 label。

### 24.5 SQLite 写入竞争

缓解：最新值模型、短事务、WAL、busy timeout、批量后台清理以及不在数据库事务中读取日志文件。初始版本不增加 batch ingest；只有实测证明需要时才扩展。

### 24.6 动态 Agent 凭据泄露

风险：Web 添加 Agent 会让 Manager 持有高权限 Agent token。

缓解：只经 HTTPS 接收、validate 不持久化、独立 `0600` credential file、SQLite 只保存 reference、响应和审计脱敏、备份文档明确 credential directory 的敏感级别。

### 24.7 Discovery 退化成通用端口扫描器或 SSRF

风险：任意网络/端口输入可被滥用于探测内网服务。

缓解：只允许本地命名 Scope、private CIDR、固定端口、bounded concurrency/time、精确 fingerprint、无 redirect、DNS 结果复检，并要求 token/TLS 二次验证。

### 24.8 Fleet polling 放大负载

风险：Agent 数量增加时，Manager 频繁 fan-out 可能产生连接风暴。

缓解：summary probe 独立于详情、bounded semaphore、jitter、15 秒默认周期、45 秒 stale window、标签页隐藏时前端停止额外 polling、详细数据按需加载。

### 24.9 Agent-only 与 Manager UI 行为分叉

风险：维护两套页面会重复逻辑并产生不一致。

缓解：共享 feature 与 App Shell，Runtime Capabilities 只决定路由入口和可见功能；禁止复制 Terminal/Services/Observations 组件。

## 25. 已确认设计决策

1. 采用模块化单体，不采用微服务。
2. 每台受管理 Server 运行一个 Agent。
3. Agent 可以独立直接登录；需要多 Agent 管理时，浏览器登录轻量 Manager，并且只通过 Manager 访问 Agent。
4. Agent 提供真实 PTY，权限由 Agent 运行账号和 Linux sudoers 决定。
5. 采集调度放在 Agent 外部，由本地程序、cron 或 systemd timer 完成。
6. Agent 接收并保留最新 Observation，不保存长期时序历史。
7. Observation 具有独立 TTL 和可扩展 `details` JSON object。
8. Log 只保存 `{path, last_updated, observed_at, ttl_seconds}` 及 Agent 生成的元数据。
9. 远端通过稳定 Log ID 按需读取有界 tail，不能传入任意 path。
10. Log allowed roots 只由 Agent 本地配置控制，远端无权扩大。
11. Prometheus 负责远端数值采集和长期时序历史。
12. 原始日志内容不进入 SQLite、审计或 Prometheus。
13. Manager 使用 SQLite 作为动态 Agent Registry 权威源，YAML Agent 列表只做一次性迁移。
14. Manager 把每个 Agent token 存入独立 `0600` 文件，浏览器永远看不到 Agent token。
15. Discovery 只扫描管理员本地配置的命名 Scope，并且 Candidate 必须逐个验证后添加。
16. Fleet 页面使用 data-dense table + drill-down；Agent 详情使用 URL 和二级页签，不使用全局 Agent selector 代替导航。
17. 前端使用 React Router + TanStack Query + feature ownership，不增加 Redux 或大型 UI framework。
18. 视觉采用中性、数据密集、无障碍优先的企业工具风格，不采用荧光终端主题或装饰性 dashboard。
