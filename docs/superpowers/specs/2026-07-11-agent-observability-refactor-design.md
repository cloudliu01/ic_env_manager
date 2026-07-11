# IC Env Guard Agent 与 Fleet Console 可扩展观测及前端重构规格

**状态：** Draft for review

**日期：** 2026-07-11

**目标版本：** Agent API v2 / Manager API v2

**范围：** `agent` 与 `control-plane`（产品界面称为 Manager）运行模式

## 1. 摘要

本次重构保留一个代码库和一个安装包，但明确两种可组合的运行方式：每台 Linux Server 上运行可独立使用的 Agent；需要集中管理时，额外运行一个轻量 Manager。Agent 保留浏览器登录和 PTY Shell，并提供本地数据写入 API、SQLite 最新状态存储、远端查询 API 和 Prometheus 兼容导出。Manager 提供 Agent 注册、删除、验证、状态探测、受控网络发现、API/Terminal 代理和统一 Fleet Web Console。

Manager 默认通过一次性 SSH enrollment 证明操作者已经能以 Agent 所在主机的现有 Linux 用户登录，并为该 Manager 注册独立、可撤销的 Agent token；之后的查询、Terminal 和管理操作继续使用 HTTP/WebSocket，不建立长期 SSH tunnel。该流程不要求 Manager 与 Agent 用户名一致，不创建专用 Linux 用户，也不依赖 NIS、LDAP 或跨网络统一 UID。Manager 以普通用户运行时可以非交互调用系统 OpenSSH；以 systemd 服务运行时由当前用户 CLI 完成同一 enrollment，并通过本地 Unix socket 把结果交给 Manager。现有 Agent token 表单保留为兼容回退。

监控逻辑不内置到 Agent。Shell、Python 程序、cron job、systemd timer 或其他本地工具负责采集数据，再把最新 Observation 或 Log Source 元数据提交给 Agent。Agent 负责输入校验、TTL、持久化、权限控制、查询和导出。

Local Producer 通过独立的 loopback HTTP listener 提交数据，不使用 token。该边界明确把 Agent 主机上的全部本地用户和进程视为可信写入者；远端 Public/Manager listener 不挂载 Ingest routes。

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
4. 本地程序可以通过独立 loopback HTTP API 无 token 上报最新 Observation。
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
15. 发现结果必须经过 SSH enrollment 或显式 Agent token 验证才能加入注册表，扫描成功不等于获得 Agent 权限或注册成功。
16. Manager 用户可以从 Agent 详情进入 Terminal、Services、Observations、Logs、Metrics 和 Audit，不需要重新输入 Agent token。
17. Manager 不保存 Observation/Prometheus 长期历史，只缓存 Fleet 页面所需的最新状态摘要。
18. 普通用户进程和 systemd Manager 共用同一 enrollment 契约；Manager daemon 不读取、上传或保存个人 SSH 私钥。

### 3.2 工程目标

1. Auth、Terminal、Observations、Logs、Metrics、Audit、Storage 和 Bootstrap 模块具有明确边界。
2. 领域逻辑不依赖 FastAPI、SQLAlchemy 或 Prometheus Client 的具体实现。
3. 每个模块可以使用内存适配器独立执行单元测试。
4. HTTP、SQLite 和 Prometheus 通过端口/适配器连接到应用层。
5. `create_app()` 不再直接构造所有业务对象；组合集中在 Bootstrap Composition Root。
6. Agent v2 契约使用明确版本、统一错误格式和可重复的 contract tests。
7. Agent Registry、Enrollment、Discovery、Availability 和 Agent Proxy 具有独立应用服务和 Repository/adapter 接口。
8. React 前端按 feature 组织，路由状态进入 URL，远端数据状态由 query cache 管理。
9. Terminal 状态按 `agent_id + terminal_id` 隔离，切换页面或 Agent 时不得串线。
10. Agent-only 和 Manager UI 复用同一套 feature，但通过 Runtime Capabilities 决定入口和可见导航。

## 4. 非目标

本次重构不包括：

- 将模块拆成独立微服务或容器；
- 将 Manager 拆成多个微服务或引入分布式服务发现；
- 让浏览器直接访问 Agent token、Agent 内网地址或执行端口扫描；
- 扫描用户任意输入的 CIDR、端口范围或公网；
- 仅凭 Discovery 自动授权、自动写入凭据或自动安装 Agent；用户显式启动 enrollment 后允许自动验证和保存；
- 在 Manager 中复制 Prometheus 时间序列或建立第二套监控历史库；
- 在 Agent 内实现通用调度器、告警引擎或规则引擎；
- 替代 cron、systemd timer、Prometheus、Alertmanager 或 Grafana；
- 在 SQLite 中保存长期指标历史；
- 将完整日志内容写入 SQLite 或 Prometheus；
- 允许远端 API 提交任意文件路径进行读取；
- 通过 Observation API 执行任意命令；
- 在第一版实现 PAM、LDAP、OIDC 或浏览器用户到多个 Linux 账号的动态映射；
- 为 Local Producer 实现 token、per-user identity、namespace ACL 或本机用户隔离；本规格明确采用整机信任模型；
- 在第一版实现批量共享 Agent token、自动证书签发或 Agent 主动注册；这些能力只有在单 Agent 凭据流程稳定后才能单独设计。
- 让 Agent HTTP API 返回 `authorized_keys`、客户端公钥或私钥路径，并自行实现 SSH challenge/signature；密钥选择和持有证明交给系统 OpenSSH。
- 把 SSH 作为常驻 Agent API、Terminal WebSocket 或 Prometheus scrape tunnel；SSH 只用于 enrollment 和显式恢复操作。
- 要求 Manager 与 Agent 使用同名 Linux 用户、相同 UID、NIS 或统一目录服务。

## 5. 核心架构

```text
Standalone usage
  Remote Browser ──HTTP(S)──> Agent Public HTTP / Web UI

Fleet usage
  Remote Browser ──HTTP(S)──> Manager Public HTTP / Fleet Web UI
                                  │
                                  ├── Agent Registry + latest status cache
                                  ├── bounded Discovery jobs
                                  ├── one-time SSH enrollment orchestration
                                  ├── Agent API proxy
                                  └── Terminal WebSocket proxy
                                            │
                                            └──HTTP(S)/WS(S)──> Agent(s)

Enrollment control path
  Existing Linux user / Manager process
      └──system OpenSSH──> existing Agent Linux user
              └──fixed ic-env-guard enrollment helper

Local collectors
  └── localhost HTTP（no token）
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
  ├── Enrollment Orchestrator
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
- Public HTTP 默认绑定 `127.0.0.1`；远端暴露必须显式开启。默认要求 HTTPS 或受信任的 TLS 反向代理；只有显式启用 `trusted_lan_http` 且接受本规格第 6.2 节信任假设时，才允许在配置的私有 CIDR 内使用 HTTP。
- Local Ingest HTTP 单独绑定 `127.0.0.1` 或 `::1`，不得配置为非 loopback 地址；它不执行 token 认证，并显式信任本机全部用户和进程。
- Public HTTP 和 Local Ingest HTTP 可以由同一主进程启动两个监听器，但二者使用不同端口、暴露策略和路由集合。
- 默认端口：Public `8765`，Local Ingest `8766`。
- Manager 只暴露自己的浏览器会话；Agent 凭据始终保留在 Manager 服务端。

### 5.2 模块边界

建议目标包结构：

```text
backend/ic_env_guard/
  bootstrap/       配置加载、Composition Root、进程生命周期
  auth/            浏览器/Manager credential 认证和权限上下文
  enrollment/      Agent/Manager enrollment 用例、系统 SSH/CLI/Unix socket 适配器、激活和撤销
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
- Discovery 只产生尚未授权给当前 Manager 的 Candidate，不得直接调用 Registry 写入。
- Agent Proxy 只接受 Registry 中已启用且已验证的 Agent ID，不接受用户提供的任意 upstream URL。
- Enrollment 只通过固定协议向 Registry 交付已验证结果；SSH adapter 不读取 Agent 业务数据，Registry 也不执行 SSH。
- SSH 必须通过参数数组调用系统 `ssh`，不得拼接 shell command；浏览器不能提交任意 SSH option、identity path 或远端命令。

## 6. 身份认证与 Web Terminal

### 6.1 浏览器认证

- Agent v2 继续支持现有 bearer token 管理员登录，以降低迁移成本。
- Manager 使用独立管理员 token；Manager 登录凭据和任何 Agent token 不得相同。
- 浏览器登录 Agent 时只能管理该主机；浏览器登录 Manager 时通过 Manager 访问整个 Fleet。
- token 文件必须是普通文件，权限不得允许非 owner 读取。
- Public HTTP 非 loopback 暴露默认必须启用 HTTPS 或部署在受信任 TLS 反向代理之后。显式 `trusted_lan_http` profile 是例外：它把传输机密性委托给受控内网，UI 和部署文档必须持续显示未加密警告。
- 登录失败必须限流并写入安全审计。
- token 和 Terminal ticket 不得出现在日志、审计、metrics、API 错误或前端持久化状态中。
- v2 将当前认证身份明确命名为 `local-admin`；多用户身份系统不在本规格范围内。

### 6.2 Manager 到 Agent 的身份

#### 6.2.1 内网信任边界

本规格支持一个显式的 trusted-LAN 部署 profile，其假设是：Discovery 或指定地址返回的 IC Env Guard fingerprint 来自真实 Agent，Manager 到 Agent 的网络路径不存在 Agent 冒充、主动中间人或被动抓包。普通内网主机仍可以主动连接 Agent 端口，因此它们不自动获得 API、Terminal 或 sudo 权限。

该假设允许简化 Agent 服务端身份 bootstrap，但不能取消 Manager 身份认证：Discovery 只证明“这里是 Agent”，SSH enrollment 证明“这个 Manager 的操作者已经被远端现有 Linux 用户授权”。若网络不能满足无冒充和无抓包假设，必须改用 verified-TLS transport，并按正常 OpenSSH host key/Host CA 策略核对服务器身份。

Agent fingerprint、Discovery 和 enrollment info 不得返回 `authorized_keys`、客户端公钥、私钥路径或 token。应用不扫描或解析 `~/.ssh/id_*`；客户端密钥选择、签名和持有证明全部交给系统 OpenSSH。

#### 6.2.2 默认 SSH enrollment

添加 Agent 的默认方式是一次性 SSH enrollment：

1. 用户提供或从 Discovery 预填 Agent base URL，并指定 `ssh_user`、`ssh_host` 和 `ssh_port`；远端用户名允许与 Manager 本地用户名不同。
2. Manager 创建一个 10 分钟有效、有界且一次消费的 pending enrollment；此时不写 Agent Registry。
3. enrollment adapter 通过参数数组调用系统 `ssh`，执行固定的 `ic-env-guard agent enroll-manager` helper；不得使用 `shell=True`、任意远端命令或浏览器提交的 SSH option。`manager_id` 和 enrollment nonce 通过有界、版本化 JSON stdin 传入，不进入远端 command line。
4. 远端 helper 必须以 Agent 运行账号执行，或由现有 sudoers 明确允许调用受保护的 Agent enrollment socket；项目不创建用户、不修改 sudoers，也不自动编辑 `authorized_keys`。
5. Agent 生成 256-bit 随机 Manager-specific token，只通过 SSH stdout 返回一次，并在 pending 状态只保存 verifier/hash。pending token 只能读取 capabilities/summary 并完成激活，不能创建 Terminal 或执行 Service mutation。
6. Manager 使用该 token 验证 endpoint、`instance_id`、API version、capabilities 和 summary；用户确认保存后才激活 token，并把 Agent 写入 Registry。
7. 激活后 Agent 持久化 token hash，Manager Credential Store 保存发送请求所需的 token 明文。未激活的失败、取消或超时在确认 pending expiry 后删除临时 credential；已激活或状态未知时保留 durable journal/reference 直到 Registry commit 或 revoke 确认。

Agent enrollment contract：

```text
GET    /api/v2/capabilities
GET    /api/v2/summary
GET    /api/v2/manager-credentials
POST   /api/v2/manager-credentials/{credential_id}/activate
DELETE /api/v2/manager-credentials/{credential_id}
```

Pending token 只能调用 capabilities/summary 和激活自己的 credential；active Manager token 可以撤销自己以及相同 `manager_id` 的旧 credential，Agent 本地 `local-admin` 可以列出和撤销任意 Manager credential。List 只返回 credential ID、Manager ID、state 和时间字段；Public API 永不列出 token hash。远端 SSH helper 通过受保护的本地 enrollment socket 调用同一个 Enrollment Service，不直接修改 SQLite。

Activate 和 revoke 必须按 credential ID 幂等：重复激活同一 active credential 返回成功；重复撤销已 revoked credential 返回成功但不泄露其他 metadata。Activate 要求 pending token、credential ID 和 enrollment ID 全部匹配；revoke 不比较 enrollment ID，只允许 active token 撤销相同 `manager_id` 的 credential，或允许本地 `local-admin` 撤销任意 credential；其他情况返回 `403`。

SSH helper 使用固定的 versioned JSON 协议。stdin 最大 4 KiB，只接受一个对象：

```json
{
  "protocol": "manager-enrollment.v1",
  "manager_id": "2b576727-4f36-4f08-b90b-e8cbe98ebc80",
  "enrollment_id": "01J2W4..."
}
```

stdout 最大 8 KiB，只返回一个 JSON object，不允许 banner 或附加文本：

```json
{
  "protocol": "manager-enrollment.v1",
  "instance_id": "a670d8f8-6074-4d7e-a118-15f445a25d72",
  "credential_id": "9dcb5e43-14c0-4056-aaae-fbaf94c27211",
  "token": "write-only-pending-token",
  "expires_at": "2026-07-11T10:10:00Z"
}
```

Agent 把 pending credential 绑定到 `manager_id + enrollment_id`；重复或过期 enrollment ID 不重新返回 token。Manager 必须核对 protocol、TTL、credential ID、helper `instance_id` 和后续 capabilities `instance_id`，并把 remote credential ID 写入 durable enrollment journal。stderr 仅用于有界安全诊断，不得包含 token。

SSH host key 策略与 HTTP transport 分开，因为 TLS certificate 不认证 SSH server：

- `trusted_lan_http`：auto、CLI 和 service-key 首次连接均可使用 `StrictHostKeyChecking=accept-new`；已记录 key 改变必须失败。
- `verified_tls` auto：使用 `StrictHostKeyChecking=yes`；未知 host key 返回 `ssh_host_key_unknown` 并引导 CLI，不能在 Web 后台自动接受。
- `verified_tls` CLI：使用正常 `ask`/known_hosts/Host CA 流程，由当前用户在终端确认；Manager 不代替用户点击确认。
- `verified_tls` service key：要求预配置 Manager owner 的 known_hosts 或 Host CA，并使用 strict checking；缺失时 enrollment fail closed。

所有 profile 下已记录 host key 变化都必须失败为 `ssh_host_key_changed`，不能静默覆盖。SSH 只承载 enrollment 和显式凭据恢复，不承载日常 Agent API、Terminal WebSocket 或 Prometheus 流量。

SSH adapter 固定设置 `PreferredAuthentications=publickey`、`PasswordAuthentication=no`、`KbdInteractiveAuthentication=no`、`ClearAllForwardings=yes`、`RequestTTY=no`、`PermitLocalCommand=no`、`CanonicalizeHostname=no`，并把 ProxyCommand/ProxyJump 设为 none。Manager 先解析并校验目标 IP，再以 command-line `Hostname=<validated-ip>`、`User=<validated-user>`、`Port=<validated-port>` 和稳定 `HostKeyAlias` 覆盖用户 config；调用前使用 `ssh -G` 验证 effective target 和禁用项，避免第二次 DNS、config rewrite 和目标 TOCTOU。用户 config 只贡献 key/IdentityAgent 等非路由选择；CLI 可以在本机交互解锁加密私钥，但不得向 Web/Manager 传递 passphrase。stdout/stderr、执行时间和响应 JSON 都必须有界。

#### 6.2.3 两种 Manager 运行方式

- **普通用户进程：** Manager 使用自己的进程环境调用系统 OpenSSH，并以 `BatchMode=yes` 自动尝试现有 `ssh-agent`、SSH config 和可非交互使用的 key。加密 key 未加入 `ssh-agent`、需要密码或 sudo 交互时，自动路径立即失败并切换到 CLI 指引；Web 后台不得等待密码输入。
- **systemd 服务：** Web 创建 pending enrollment 并显示不含 secret 的 CLI 命令。当前 Linux 用户运行 `ic-env-guardctl agent enroll --manager-socket ... --enrollment-id ... --ssh user@host`；CLI 使用该用户自己的 OpenSSH 环境，捕获并解析有界 JSON stdout 但不回显 token，再通过 Manager 本地 Unix socket 提交结果。socket 必须验证 peer credentials、owner/group/mode、enrollment ID、TTL 和单次提交，拒绝 replay。Agent token 不进入 CLI 参数、shell history 或浏览器。
- **可选无人值守：** systemd Manager 可以配置一把 Manager 专用 Ed25519 SSH key，但不创建专用远端用户。管理员把公钥安装到远端现有用户的 `authorized_keys`，并使用 forced command、禁止 PTY、agent/X11/port forwarding 等限制，使该 key 只能进入 enrollment helper。项目提供精确配置模板和 Agent 端检查命令，但不自动分发或修改该文件，也不声称 Manager 能从网络证明远端限制正确。

三种路径产生完全相同的 Manager-specific token 和 Registry 记录；差异只存在于 SSH adapter。Manager 不读取、上传、复制或保存个人私钥，也不要求 Manager 与 Agent 共享用户名、UID、NIS、LDAP 或其他用户目录。

普通用户已有的 Ed25519、RSA/SHA-2、ECDSA、FIDO 或 SSH certificate 由本机 OpenSSH 按系统策略处理；应用不硬编码 RSA，也不重新启用 legacy `ssh-rsa`/SHA-1。新建的可选 service key 使用 Ed25519。

#### 6.2.4 长期凭据与传输

- Manager-specific token 在 Agent 上形成独立、可撤销的认证主体 `manager:<manager_id>`；初版授予与 `local-admin` 相同的 Manager API 能力。Local Ingest 不处理任何 bearer token，并且只存在于独立 loopback listener。
- Agent 支持多个独立 Manager credential；轮换时先验证新 token，再原子替换 Manager credential reference，最后撤销旧 token。
- Manager 将 token 原子写入 `credential_directory` 下随机 opaque 文件名的独立文件。文件由 Manager 运行账号拥有、权限为 `0600`；SQLite 只保存 credential reference。
- Credential Store 写入使用同目录临时文件、`fsync`、`chmod` 和 atomic rename；失败时请求远端 revoke，只有确认未激活/已撤销后才删除本地 token。启动 cleanup 只能删除既无 Registry reference、也无非 terminal enrollment/rotation journal reference 的 orphan。
- Manager 代理请求时从 Credential Store 读取 token，并通过 server-to-server `Authorization` header 发送；浏览器永远看不到该 header。
- 默认 transport 是 verified TLS。显式配置的 `trusted_lan_http` 允许在 `allowed_agent_cidrs` 内使用 HTTP/WS，但 token、Terminal 内容和日志响应此时没有传输加密；UI 不允许临时开启或隐藏该警告。
- Manual Agent admin token 只作为旧配置导入和高级恢复路径保留；默认 Add Agent UI 不要求用户复制 token。该 token 仍遵循 write-only、验证后持久化和不回显规则。
- 在线删除 Agent 时先 best-effort 撤销远端 Manager credential，再删除本地 Registry 和 credential；Agent 离线时允许用户明确确认 local-only removal，并提示远端 credential 仍需在 Agent 本地撤销。

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
- 不需要 token、cookie 或 `Authorization` header；listener 的 loopback 网络边界就是唯一访问边界。
- 实际 TCP peer 必须是 loopback；忽略 `Forwarded`、`X-Forwarded-For` 和类似代理 header，部署文档禁止反向代理或端口转发暴露该 listener。
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
- 不需要 token、cookie 或 `Authorization` header，并遵循与 Observation Ingest 相同的 loopback-only 规则。
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
    "summary.v2",
    "manager-enrollment.v1"
  ]
}
```

`instance_id` 使用标准小写 UUID。Agent identity file 不是 secret，但必须只允许管理员修改；文件缺失或格式错误时 Agent 启动失败并给出恢复指引，不能每次启动生成新 ID。

Manager 首次启动时也生成稳定小写 UUID `manager_id`，持久化在 Manager SQLite metadata 中。重新 enrollment 使用相同 `manager_id`，Agent 可以识别这是凭据轮换而不是一个未知 Manager；恢复 Manager 数据库时必须连同该 ID 和 Credential Store 一起恢复。

Manager Registry 保存：

- Manager 路由使用的稳定 `agent_id`；
- Agent 自报且全局唯一的 `instance_id`；
- 管理员可修改的 `display_name`；
- 规范化 endpoint；
- Manager-specific `credential_ref` 和远端 opaque `credential_id`；
- 本地 transport profile reference；
- enrollment method（`ssh_auto`、`ssh_cli`、`ssh_service_key` 或 `legacy_admin_token`）；
- enabled 状态；
- 创建/更新时间；
- 最近一次验证的版本和 capabilities。

`agent_id` 是 Manager 内不可变的路由 key：YAML import 保留现有配置 ID 以兼容旧 URL；Web 新增时默认使用 Agent `instance_id`。`instance_id` 单独存储并用于跨地址去重，用户只能修改 display name，不能修改这两个身份字段。

Registry 不保存个人 SSH key path、`SSH_AUTH_SOCK`、`authorized_keys` 内容或任意 SSH option。SSH target 只存在于短期 enrollment job 和脱敏审计中；完成 enrollment 后的正常连接只依赖 endpoint、transport profile 和 Manager-specific token。

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
- 合法配置的 `trusted_lan_http` 不把 connection status 降为 `degraded`；transport security 是独立、持续可见的属性和警告。
- UI 必须显示状态文字和图标，不能只依赖颜色。

### 11.2 Registry API v2

```text
POST   /api/v2/agent-enrollments
GET    /api/v2/agent-enrollments/{enrollment_id}
POST   /api/v2/agent-enrollments/{enrollment_id}/cancel
POST   /api/v2/agents/validate                    # legacy/manual recovery only
POST   /api/v2/agents
GET    /api/v2/agents
GET    /api/v2/agents/{agent_id}
PUT    /api/v2/agents/{agent_id}
DELETE /api/v2/agents/{agent_id}
POST   /api/v2/agents/{agent_id}/probe
POST   /api/v2/agents/{agent_id}/enabled
POST   /api/v2/agents/{agent_id}/credential-rotation
GET    /api/v2/fleet/overview
```

默认 enrollment 请求：

```json
{
  "base_url": "https://eda-host-01.example:8765",
  "display_name": "EDA Host 01",
  "transport_profile_id": "eda-internal-tls",
  "ssh": {
    "user": "edaops",
    "host": "eda-host-01.example",
    "port": 22
  }
}
```

- `transport_profile_id` 必须引用 Manager 本地配置；API 不接受 CA 文件路径、任意 scheme 或关闭验证的布尔值。
- 从 Discovery 进入时请求额外包含 opaque `discovery_result_id`。Manager 必须验证该结果仍存在、状态为 new，且 URL、resolved IP、port 和 transport profile 与请求完全匹配；没有该字段时 Registry source 为 `manual`。
- SSH host 必须解析到 `allowed_agent_cidrs`，并与 base URL 的已验证地址集合相交；初版不支持浏览器提供 ProxyJump、改变有效目标的 SSH config 或不同目标的 bastion。
- Manager 根据运行环境选择 `ssh_auto`、`ssh_cli` 或本地配置的 `ssh_service_key`；浏览器不能指定 identity file 或远端命令。
- enrollment job 状态为 `pending`、`running`、`awaiting_cli`、`verifying`、`verified`、`failed`、`expired`、`cancelled` 或 `consumed`。job 默认 10 分钟过期，最大并发和总数由本地配置限制。
- `GET enrollment` 可以返回不含 secret 的 CLI command、阶段状态、安全错误、Agent preview 和到期时间；不得返回 token、SSH output、private key path 或 `SSH_AUTH_SOCK`。
- Manager 本地 enrollment socket 是内部 adapter，不属于 Public API；它只接受匹配 enrollment ID 的有界单次提交。
- List 支持 `query`、`connection_status`、`workload_status`、`capability`、`limit`（默认 100，最大 1000）和 opaque cursor。
- Create 返回 `201`，Update/Enable/Probe 返回 `200`，Delete 返回 `204`。

添加流程：

1. Web 表单在本地完成 URL shape 校验。
2. `POST /agent-enrollments` 创建短期 job。普通用户 Manager 先自动 SSH；systemd Manager 没有可用 service key 时返回 `awaiting_cli` 和可复制命令。
3. SSH adapter 获取 pending Manager-specific token 后，只在 Manager 服务端内存和临时 owner-only credential file 中使用它请求 `/api/v2/capabilities` 和 `/api/v2/summary`。
4. Manager 返回 Agent ID、name、版本、transport 警告、capabilities 和 summary preview，不返回 token。
5. 任一 connection/SSH 输入发生变化后取消旧 job；verified enrollment 只能消费一次。
6. 用户确认后，`POST /agents` 只提交 `{ "enrollment_id": "...", "display_name": "..." }`。Manager 重新确认 job 未过期、输入未改变、identity 未重复，在同一 journal transaction 中持久化 `save_requested` 和最终 display name，再激活远端 credential 并写 Credential Store/Registry；新 Agent 默认 enabled。
7. Agent `instance_id` 或 normalized endpoint 已存在时返回 `409 agent_already_registered`。未签发远端 credential 或已确认 pending expiry/revoke 的失败路径删除临时 credential；active revoke 结果未知时保留 journal、token reference 和 residual 告警，直到可重试清理。

Enrollment 是可恢复 saga，不假装成跨主机数据库 transaction：Agent 返回 pending credential 后，Manager 必须先 fsync 临时 credential 并提交包含 remote credential ID 的 journal，才能继续验证；激活前必须持久化 `save_requested`。若在远端激活后、本地 Registry commit 前崩溃，重启时使用 journal 和保留 token 完成 commit 或撤销，不能把文件当 orphan 删除。每个 phase boundary 都必须幂等，terminal cleanup 只有在 Registry commit 或远端 revoke 已确认后才能删除 token file/reference。

Enrollment preview 必须分别报告 `network`、`ssh`、`transport`、`authentication`、`protocol`、`identity`、`capabilities` 和 `readiness` 阶段。SSH enrollment 的 network/SSH/transport/auth/protocol/identity 是添加 gate；Summary/readiness 失败作为 warning。Legacy v1 recovery 按下文明确降级 identity/readiness，不得把缺少 v2 字段误报为安全验证成功。错误只包含稳定 code 和安全 message，不返回原始 socket exception 或 SSH stderr 全文。

`POST /agents/validate` 保留给 `legacy_admin_token` 导入和高级恢复。请求包含 base URL、write-only token 和 transport profile；成功后创建同样短期、一次消费的 verified enrollment job，并只返回 `enrollment_id` 和 preview，随后仍由 `POST /agents` 消费。Token 只存在于 Manager 内存/owner-only 临时 credential file；任何响应、列表、detail、日志或审计均不得回显。默认 Add Agent UI 不调用该接口。

Legacy v1 validation profile 明确定义为：使用该 token 调用现有 authenticated `GET /api/capabilities`，接受受支持的 `api_version: "1"` 和 v1 capability IDs，不要求 `/api/v2/summary` 或 `instance_id`。Network、transport、authentication 和 v1 protocol compatibility 仍是 gate；identity 阶段返回 `legacy_identity_unavailable` warning 而不是失败。保存时 `instance_id=null`、`agent_id` 由 Manager 生成、只按 normalized endpoint 去重，connection status 为 `degraded`、workload status 为 `unknown`，并且只开放 Agent 实际声明的 v1 routes。UI 必须标记“无法跨地址验证身份”；改变 endpoint 必须重新提交 legacy token 并明确确认。升级到声明 `manager-enrollment.v1` 的 Agent 后，应通过 SSH enrollment 建立稳定 identity 和独立 Manager credential。

更新规则：

- 修改 display name 或 enabled 不要求重新 enrollment。
- 修改 base URL 或 transport profile 时，Manager 使用现有 credential 验证相同 `instance_id`；成功后原子替换并增加 Registry revision。认证失败或 identity 改变时要求重新 enrollment。
- Credential rotation 复用 durable enrollment journal，并在开始时保存旧 local/remote credential reference。新 credential 验证并激活成功后才替换 Registry reference；相同 `manager_id` 的新 token 可以撤销旧 credential。旧 credential file 只有在撤销确认后才删除；失败或崩溃时 journal 保留 residual 状态供重试并在 UI 告警，不能丢失旧撤销能力。
- 失败更新不得破坏原有可用配置。
- `DELETE` 的 UI 文案为“Remove from Manager”，不得暗示会卸载远端 Agent 或删除 Agent 本地数据。
- Agent 存在活跃 Terminal proxy 时删除返回 `409 agent_in_use`；用户先关闭 Terminal 后重试。
- 在线删除先尝试撤销 Agent 端的 Manager credential；离线时默认返回可恢复冲突，用户明确确认 `local_only` 后才删除本地 Registry/credential，并看到远端 credential 仍需本地撤销的警告。Control-plane Audit 永久保留。
- `legacy_admin_token` 没有远端独立 credential 可撤销，删除只清理 Manager 本地副本，并明确提示它不会轮换 Agent 原有 admin token。
- list/detail API 必须返回 enabled、disabled、unknown、ready、degraded 和 unavailable Agent，不得因为 Agent 离线而隐藏。
- Offline/disabled Agent 仍可读取 Registry Overview/Settings、重新 enrollment、执行 local-only removal；只有需要 upstream 的 Terminal/Service/Log 操作被禁用。
- 任何 Registry 响应不得包含 token、credential path 或 Authorization header。
- Probe 开始时读取 Registry revision；完成写 status 时 revision 必须仍匹配，避免旧地址的迟到 probe 覆盖新配置状态。
- 动态 URL 校验、enrollment、probe 和实际代理必须共用 `AgentTargetPolicy`：DNS 全部解析结果位于 allowed CIDR，禁止 self target/metadata/link-local/multicast/unspecified/reserved、禁止 redirect，并把连接 pin 到已验证 IP，同时保留原 hostname 用于 TLS SNI/HTTP Host。verified-TLS 防止 DNS rebinding；trusted-LAN profile 依赖已声明的内网无冒充假设。
- `source=config_import` 且 endpoint 未改变的旧 Agent 继续按原启动配置安全规则运行；一旦通过 Web 修改 endpoint，该记录转为动态策略并必须满足 `allowed_agent_cidrs`。此兼容例外不得用于新增 Agent。

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
        endpoints:
          - port: 8765
            transport_profile_id: eda-lan-http
          - port: 9443
            transport_profile_id: eda-internal-tls
```

约束：

- UI 只能选择预配置 Scope，不能提交任意 CIDR、任意端口范围、scheme、transport profile 或 URL。
- `scopes: []` 是默认值，此时 Discovery API 返回 feature disabled；管理员必须在本地配置显式启用。
- 单个 Scope 最多 256 个地址、8 个预配置 endpoint；更大网络必须由管理员拆分。
- 默认最大并发连接 32，单地址连接超时 500 ms，HTTP fingerprint 超时 2 秒，整个 job 最长 120 秒。
- Manager 只执行 TCP connect 和 IC Env Guard fingerprint 请求，不进行通用 banner grabbing。
- 非私有地址、Manager 自身地址、multicast、unspecified、reserved 和 link-local 地址必须拒绝。
- Agent 的公开 `/healthz` 响应增加 `X-IC-Env-Guard-Agent: 2` header；发现只确认产品 fingerprint，不返回 Agent token、SSH 用户、公钥、`authorized_keys`、Terminal 或敏感配置。
- trusted-LAN threat model 允许把匹配 fingerprint 的结果视为真实 Agent，但它仍是尚未授权给当前 Manager 的 Candidate；必须由用户显式启动 SSH enrollment 或 legacy token recovery 才能加入 Registry。
- Discovery 不调用 SSH、不尝试已有 key、不创建 pending token，也不批量注册；SSH 只在用户选择单个 Candidate 后执行。

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
- IP、port 和 transport profile ID；
- fingerprint API version；
- 首次/最近发现时间；
- `new` 或 `already_registered`；
- enrollment 状态（`enrollment_required`、`enrolling`、`verified` 或 `already_registered`）；
- nullable linked `enrollment_id`；
- 有界错误类别。

Discovery job 和结果保留 24 小时后清理。由 Candidate 创建 enrollment 时，Manager 在同一 SQLite transaction 中写入 linked enrollment ID；结果状态从 enrollment journal/Registry 派生，不能由浏览器提交。成功注册后 Registry `source=discovery`。UI 每秒 polling job 状态即可；初版不增加 SSE/WebSocket。批量自动添加不在范围内，每个 Candidate 单独进入 Add Agent enrollment 流程；Candidate 记录不得保存 SSH key、Agent token 或 enrollment output。

## 13. Manager 路由与聚合

- 浏览器在 Manager 模式下只请求同源 `/api/v2/agents/{agent_id}/...` 路由。
- Manager 从 path 中解析 Agent ID，再从 Registry 获取 base URL、transport profile 和 Manager-specific credential；请求体不得包含 upstream URL。
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
- 正常 HTTP/WS 代理路径不得启动 SSH 子进程或依赖 SSH tunnel。verified-TLS profile 使用 HTTPS/WSS；trusted-LAN profile 使用 HTTP/WS，并在 Agent detail 持续显示未加密状态。

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

Local Ingest 没有 producer identity；`observations.producer_id` 和 `log_sources.producer_id` 均由 Repository 强制写为常量 `local`，只为 schema/迁移兼容保留该列。

### 14.3 Agent `manager_credentials`

Agent 为每个 Manager 保存独立 credential：

```text
credential_id       TEXT PRIMARY KEY
manager_id           TEXT NOT NULL
enrollment_id        TEXT NOT NULL UNIQUE
token_hash           TEXT NOT NULL UNIQUE
state                TEXT NOT NULL
pending_expires_at   TEXT NULL
created_at           TEXT NOT NULL
activated_at         TEXT NULL
last_used_at         TEXT NULL
revoked_at           TEXT NULL
```

`state` 是 `pending`、`active` 或 `revoked`。pending credential 默认 10 分钟过期，只能访问 enrollment validation endpoints；激活后才具有 Manager API 权限。Agent 只保存高熵 token 的 hash 并使用 constant-time comparison，不保存 token 明文。允许同一 Agent 同时存在多个不同 `manager_id` credential；同一 Manager 的轮换可以短时间保留新旧两条，完成后撤销旧条目。

Credential issuance、activation、expiry 和 revocation 由 Enrollment Repository 独占；Auth 只通过公开 verifier 接口查询，不直接操作表。过期 pending row 由有界 cleanup 删除，revoked row 按安全审计保留期清理。

### 14.4 Manager `agents`

```text
agent_id            TEXT PRIMARY KEY
instance_id         TEXT NULL UNIQUE
display_name        TEXT NOT NULL
normalized_endpoint TEXT NOT NULL UNIQUE
credential_ref      TEXT NOT NULL
remote_credential_id TEXT NULL
transport_profile_id TEXT NOT NULL
enrollment_method   TEXT NOT NULL
enabled             INTEGER NOT NULL
source              TEXT NOT NULL
revision            INTEGER NOT NULL
created_at          TEXT NOT NULL
updated_at          TEXT NOT NULL
```

`source` 是 `config_import`、`manual` 或 `discovery`，描述记录从哪里进入 Registry；`enrollment_method` 是 `ssh_auto`、`ssh_cli`、`ssh_service_key` 或 `legacy_admin_token`，描述 credential 如何建立。YAML 导入固定为 `source=config_import`、`enrollment_method=legacy_admin_token`。Legacy credential 可以没有 `remote_credential_id`；新 SSH enrollment 必须保存 opaque remote credential ID 以支持轮换和撤销。Version、capabilities 和 summary 不属于配置表。

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

Manager 使用 durable `agent_enrollment_jobs` journal 记录跨 SSH、Agent API 和本地 Credential Store 的恢复状态：

```text
enrollment_id        TEXT PRIMARY KEY
manager_id           TEXT NOT NULL
state                TEXT NOT NULL
normalized_endpoint  TEXT NOT NULL
transport_profile_id TEXT NOT NULL
discovery_result_id  TEXT NULL
replace_agent_id     TEXT NULL
requested_display_name TEXT NULL
ssh_user             TEXT NULL
ssh_host             TEXT NULL
ssh_port             INTEGER NULL
enrollment_method    TEXT NOT NULL
remote_instance_id   TEXT NULL
remote_credential_id TEXT NULL
credential_temp_ref  TEXT NULL
old_credential_ref   TEXT NULL
old_remote_credential_id TEXT NULL
save_requested       INTEGER NOT NULL
expires_at           TEXT NOT NULL
last_error_code      TEXT NULL
created_at           TEXT NOT NULL
updated_at           TEXT NOT NULL
```

Journal 不保存 token、SSH output、private key path、passphrase 或 Authorization header；`credential_temp_ref` 只引用 owner-only 临时文件。内部 state 至少覆盖 `pending`、`running`、`awaiting_cli`、`credential_issued`、`verifying`、`verified`、`activation_requested`、`activated` 和 terminal states；Public API 把内部状态折叠成第 11.2 节的稳定状态集合。

SSH fields 对 `ssh_auto/ssh_cli/ssh_service_key` 必须非空，对 `legacy_admin_token` 必须为空。`requested_display_name` 在 `save_requested=true` 前可以为空，之后必须保存最终值，使 crash recovery 能精确重放 Registry row。`discovery_result_id` 与 `replace_agent_id` 分别标识 Discovery add 和 credential rotation，二者的组合及 target identity 由 Repository invariant 校验。

另有有界的 `discovery_jobs`、`discovery_results`，其中 Discovery result 可以引用 enrollment ID。Manager metadata 保存稳定 `manager_id`。这些数据属于各自 Repository，不与 Agent 本地 `observations` 或 `log_sources` 混用。

### 14.5 数据库运行规则

- 继续使用 migration-managed SQLite。
- 启用 foreign keys、busy timeout 和 WAL mode。
- 每次 upsert 使用单个短事务。
- tail 日志文件时不得持有数据库事务。
- 数据库异常返回稳定错误，不在响应中暴露 SQL、文件路径或内部堆栈。
- Observation、Log Source、Manager Credential、Terminal metadata 和 Audit 使用独立 Repository。
- Credential orphan cleanup 必须同时检查 Agent Registry 和所有非 terminal enrollment/rotation journal，不能删除恢复尚需使用的 token file。
- Manager startup 扫描非 terminal journal：未激活的远端 pending credential 可以继续验证或等待 TTL；`activation_requested/activated` 且 `save_requested=true` 的 job 使用保留的 credential 完成 Registry commit，或在用户已取消时撤销；任何无法自动恢复的 residual credential 进入显式告警和可重试 cleanup，不得静默丢弃唯一 credential reference。

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

### 16.1 Producer 本地信任边界

- Local Ingest API 只监听 `127.0.0.1` 或 `::1`，不执行应用层认证。
- Agent 主机上的所有本地 Linux 用户和进程都被允许创建或覆盖 Observation、注册 Log Source；这是显式产品假设，不描述为用户级隔离。
- Agent 不读取 token、cookie、`Authorization`、`Forwarded` 或 `X-Forwarded-For` 来决定 Ingest 权限。
- `producer_id` 由 Agent 固定写为 `local`。请求体或 header 中提供 `producer_id` 必须拒绝，避免形成看似可信但可伪造的归属信息。
- 不实现 producer token、token file、token rotation、per-producer namespace 权限或 per-producer audit identity。
- 请求体大小、字段、`details`、labels、identity/series 数量、并发和 SQLite 写入限制仍然生效，用于控制错误程序造成的资源消耗。

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
  trusted_lan_http:
    enabled: false
    client_cidrs: []

enrollment:
  socket_path: /run/ic-env-guard/agent-enrollment.sock
  socket_mode: "0600"
  pending_ttl_seconds: 600
  max_pending: 16

ingest:
  bind: 127.0.0.1
  port: 8766
  max_request_bytes: 32768
  max_concurrent_requests: 16

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
  transport_profiles:
    - id: eda-internal-tls
      type: verified_tls
      ca_bundle: /etc/ic-env-guard/eda-internal-ca.pem
    - id: eda-lan-http
      type: trusted_lan_http
      allowed_cidrs:
        - 10.20.30.0/24
  enrollment:
    ssh_binary: /usr/bin/ssh
    pending_ttl_seconds: 600
    max_pending: 32
    journal_retention_seconds: 86400
    ssh_connect_timeout_seconds: 10
    socket_path: /run/ic-env-guard/manager-enrollment.sock
    socket_mode: "0660"
    socket_group: eda-admins
    service_key:
      enabled: false
      identity_file: /var/lib/ic-env-guard/ssh/id_ed25519
      known_hosts_file: /var/lib/ic-env-guard/ssh/known_hosts
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
        endpoints:
          - port: 8765
            transport_profile_id: eda-lan-http
          - port: 9443
            transport_profile_id: eda-internal-tls
```

上面的 Agent 示例保持安全默认值。若要与 `eda-lan-http` profile 配合，部署者必须在 Agent 本地把 Public bind 改为明确的非 loopback 地址、设置 `remote_bind_enabled: true`、启用 `trusted_lan_http`，并把 `client_cidrs` 限制为实际 Manager/管理网络；仅修改 Manager profile 不会自动开放 Agent listener。

配置校验必须保证：

- `mode` 是 `agent` 时才启动 Local Ingest listener；
- `ingest.bind` 必须是 `127.0.0.1` 或 `::1`；配置模型不接受 hostname、wildcard 或非 loopback 地址；
- Public 和 Ingest 端口不得相同；
- Local Ingest 不配置 token file、TLS、proxy trust 或远端 allowlist；
- `ingest.max_concurrent_requests` 必须为 1–128，达到上限返回 `503 ingest_capacity_exceeded`；
- `allowed_roots` 必须是绝对路径，启动时执行规范化和去重；
- 所有大小、数量、TTL 和间隔配置在本文规定范围内；
- 非 loopback Public bind 继续遵循现有 fail-closed 规则；默认要求 verified TLS。`server.trusted_lan_http.enabled=true` 时必须同时配置非空 private `client_cidrs`，且启动日志和 Runtime capability 明确标记无传输加密。
- `control-plane` mode 不启动 PTY、Local Ingest 或 Agent 本地数据采集模块；
- Registry、latest status、Discovery 和 Control-plane Audit 共用现有 `control_plane.audit_database` SQLite 文件，但使用各自 Repository/table；不新增第二个 Manager database path；
- `credential_directory` 必须由 Manager 账号拥有且目录权限不宽于 `0700`；
- Transport profile ID 必须唯一；`verified_tls` profile 的 CA bundle 是本地普通文件且启动时验证，路径和值不通过 Public API 暴露；
- `system-tls` 是保留 transport profile ID，使用操作系统 trust store，不得在配置中覆盖；
- `trusted_lan_http` profile 只能引用 private CIDR，必须是 `allowed_agent_cidrs` 的子集，并只接受 `http://` endpoint；`verified_tls` 只接受 `https://` endpoint。UI 不能创建、修改或临时绕过 profile；
- `allowed_agent_cidrs` 默认空列表；为空时现有导入 Agent 可继续工作，但 Web Add/Edit endpoint 和 Discovery 明确 disabled，UI 显示本地配置指引；
- 动态添加和 Discovery 只能访问 `allowed_agent_cidrs`；DNS 解析结果也必须落在允许范围内；
- Discovery Scope CIDR 必须是 `allowed_agent_cidrs` 的子网，地址数不得超过 256；
- Discovery endpoint 必须显式列出 port 和本地 transport profile，单 Scope 不超过 8 个；
- 非 loopback HTTP Agent 只有同时匹配 Manager `trusted_lan_http` profile 和 Agent 本地 `trusted_lan_http` 配置时才允许；其他 HTTP endpoint fail closed；
- Agent/Manager enrollment socket parent 必须由服务账号控制；socket mode 不宽于 `0660`，配置 group 时必须存在，并通过 Unix peer credentials 再验证提交者；
- pending enrollment TTL 为 60–900 秒，容量为 1–128；过期、取消和重复提交必须 fail closed；
- SSH binary 必须是本地配置的绝对可执行文件；远端 helper 和安全 SSH options 固定在程序中，Public API 不接受覆盖；
- service key 启用时 identity file 必须由 Manager 账号拥有、是 owner-only 普通 Ed25519 private key，known_hosts file 也必须位于 Manager owner 的真实目录。项目不自动生成、复制或安装该 key。

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
- Local Ingest 并发达到上限使用 `503 ingest_capacity_exceeded`；
- 未预期异常使用 `500 internal_error`，完整堆栈只写入受保护的服务日志。

Manager 至少定义：

- `agent_not_found`、`agent_disabled`、`agent_in_use`；
- `agent_already_registered`、`agent_identity_mismatch`、`agent_identity_conflict`；
- `agent_network_error`、`agent_tls_error`、`agent_auth_error`、`agent_protocol_error`、`agent_version_unsupported`；
- `agent_validation_required`、`agent_validation_changed`；
- `legacy_identity_unavailable`、`legacy_agent_reenrollment_required`；
- `ssh_unavailable`、`ssh_interaction_required`、`ssh_auth_failed`、`ssh_host_key_unknown`、`ssh_host_key_changed`、`ssh_remote_command_failed`；
- `agent_enrollment_not_found`、`agent_enrollment_expired`、`agent_enrollment_consumed`、`agent_enrollment_submission_rejected`；
- `agent_credential_activation_failed`、`agent_credential_revoke_failed`、`agent_local_only_confirmation_required`；
- `transport_profile_not_found`、`transport_profile_mismatch`、`trusted_lan_http_disabled`；
- `discovery_disabled`、`discovery_scope_forbidden`、`discovery_job_not_found`、`discovery_capacity_exceeded`。

Frontend 只根据 `code` 决定交互分支；`message` 用于安全显示，correlation ID 提供复制按钮以便排障。

## 19. 安全审计

安全审计继续记录：

- 登录成功和失败；
- Terminal 创建、attach、close、timeout 和异常退出；
- Log tail 请求，包括 actor、log ID、行数、结果和 source address；
- Log 路径安全拒绝；
- Local Ingest 非 loopback 配置和错误 listener/route 暴露在启动时被拒绝的事件；
- 配置加载和安全校验失败；
- 数据库迁移和存储健康变化。
- Agent validate/add/edit/enable/disable/remove/probe；
- Enrollment create/start/awaiting-cli/verify/consume/cancel/expire，以及 credential activate/rotate/revoke 和 local-only removal；
- SSH enrollment method、脱敏目标、host key changed 和 service-key 使用结果；不记录原始 SSH output；
- Discovery start/cancel/finish，包括 scope、候选数量和结果，不记录逐地址原始错误；
- Manager Agent-scoped proxy intent/outcome 和 indeterminate mutation；
- Credential Store 写入、替换或删除失败的稳定类别。

安全审计不记录：

- Terminal 输入或输出；
- 日志内容；
- Observation `details` 全文；
- token 或认证 header；
- SSH private/public key 内容、private key path、`authorized_keys`、`SSH_AUTH_SOCK` 或 enrollment socket payload；
- enrollment token、pending secret、完整 CLI command 或 SSH stdout/stderr；
- Producer 提交的任意敏感文本。

Observation/Log 正常 upsert 不逐条写入安全审计，避免高吞吐和敏感数据风险；Local Ingest 没有登录或认证失败事件。Agent metrics 记录成功、schema 拒绝、容量拒绝和存储失败计数。

Manager 的 enrollment/validate 失败、credential activation/revocation、Registry mutation、Discovery start/cancel 和 routed privileged request 必须复用现有 durable intent/outcome 模型。Audit intent 提交失败时 fail closed，不得创建 enrollment、写 Registry/credential、启动扫描或 dispatch upstream；outcome 提交失败不得把已成功的远端 mutation 伪装成失败重试。

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
  "capabilities": [
    "fleet.v2",
    "agent-registry.v2",
    "discovery.v2",
    "ssh-enrollment.auto.v1",
    "ssh-enrollment.cli.v1",
    "trusted-lan-http.v1"
  ]
}
```

Runtime endpoint 是登录前可读的低风险元数据，只返回 mode 和 capability IDs，不返回地址、Agent、版本细节或配置；响应使用 `Cache-Control: no-store`，失败时登录页显示可重试错误而不是猜测 mode。

`ssh-enrollment.auto.v1` 仅在 Manager 可以调用系统 SSH 时返回；`ssh-enrollment.cli.v1` 仅在受保护 local socket 可用时返回；配置并验证 service key 后增加 `ssh-enrollment.service-key.v1`；存在 trusted-LAN transport profile 时增加 `trusted-lan-http.v1`。这些 capability 只决定 UI 路径，不泄露 key path、socket path 或用户名。

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
- base URL 和 transport security badge；
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

Add Agent 使用三个短步骤；默认不显示 Agent token 输入框：

**Step 1 — Connection**

- Base URL；
- display name（可选，验证后默认使用 Agent name）；
- 本地预配置的 transport profile；`trusted_lan_http` 选项显示持续的“无传输加密”警告，UI 不能临时创建或绕过 profile；
- SSH user、host 和 port，host 默认取 Base URL hostname，user 必须允许覆盖以支持不同用户系统；helper text 说明该用户必须是 Agent 运行账号，或已被现有 sudoers/本地 socket policy 授权执行固定 enrollment helper；
- “Start enrollment” 是该步唯一 primary action。

**Step 2 — Enrollment**

- 普通用户 Manager 或已配置 service key 时显示自动 SSH 的阶段进度；
- systemd Manager 需要当前用户凭据时显示可复制且不含 secret 的 CLI command，并保持 `Waiting for CLI` 状态；
- 明确展示 network、SSH、transport、authentication、identity 和 protocol 阶段，不显示原始 SSH stderr；
- 加密 key 未加载、SSH 密码或 sudo 需要交互时从自动路径切换为 CLI，不在网页请求中等待输入；
- 支持 Cancel、Retry 和到期后重新创建；durable enrollment journal 允许页面刷新或 Manager restart 后在 TTL 内通过 enrollment URL 恢复安全状态。

**Step 3 — Verify & save**

- 显示 Agent ID、name、URL、transport security、version、capabilities 和当前 summary；
- 清楚显示 missing capability、重复 ID/URL 或版本不兼容；
- “Add agent” 只有 enrollment verified 且未过期/消费时可用；
- 修改 Step 1 任一字段必须取消旧 enrollment 并清除 preview。

表单要求：

- 所有字段有永久 label 和 helper text；
- URL/SSH target 在 blur 时校验 shape，服务端错误显示在对应字段或 validation summary；
- 提交中按钮 disabled 并显示进度；错误必须说明原因和恢复操作；
- 浏览器响应、form state、URL、sessionStorage、query cache、日志和 error telemetry 均不得包含 Agent token、SSH output、key path 或 `SSH_AUTH_SOCK`；
- “Import legacy token” 只出现在高级恢复入口，使用 write-only password input，并与默认 enrollment UI 分离；
- Edit 对 display name/enable 使用简单表单；改变 URL/transport 时使用现有 credential 验证相同 identity；“Rotate credential” 进入新的 enrollment flow；
- Remove 使用明确确认对话框，说明不会卸载 Agent。在线删除显示远端 credential revoke 结果；离线删除需要第二次确认 local-only removal 和远端残留 credential 风险。

### 20.5 Discovery 流程

Discovery 是独立页面：

1. 选择 Manager 配置提供的命名 Network Scope；
2. 显示将扫描的 CIDR、ports、地址数量和预计上限；
3. 用户显式启动，页面显示 progress、已检查/总数、发现数和 Cancel；
4. 结果表区分 New、Already registered 和 Enrollment required；
5. 选择一个 New Candidate 后跳转 Add Agent，并预填 candidate URL、transport profile 和 SSH host；SSH user 由用户确认，不读取或显示任何公钥。

- 页面关闭后 job 继续由 Manager 执行，返回同一 URL 可以恢复状态。
- Job 失败显示稳定错误和 Retry，不把底层 socket 错误/堆栈直接展示给用户。
- 初版不提供 “Select all and enroll”，每个 Candidate 必须由用户显式启动独立 SSH enrollment。

### 20.6 Agent 详情与 Monitoring

Agent 详情 Header 固定显示：connection status、workload status、display name、base URL、transport security、last seen 和 Probe。trusted-LAN HTTP Agent 持续显示未加密标记。二级页签为 Overview、Terminal、Services、Observations、Logs、Metrics、Audit、Settings；缺少 capability 的页签保持可见但 disabled，并解释原因。

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

Agent Web UI 复用 Terminal、Services、Observations、Logs、Metrics 和 Audit features，但不加载 Fleet、Registry 或 Discovery bundle。登录后默认进入本机 Terminal；顶部明确显示 “Standalone Agent” 和本机 agent ID，不显示无意义的 Agent selector。Agent Settings 提供最小 “Manager access” 表，只显示 Manager ID、credential ID、state 和时间，并允许 `local-admin` 撤销；不显示 token hash、SSH key 或 enrollment output。

## 21. 兼容性与迁移

### 21.1 API 版本

- 新能力只在 `/api/v2` 提供。
- 现有 `/api/agents`、`/api/fleet/overview` 和 Agent-scoped v1 proxy 在兼容期继续工作，但底层改为读取 SQLite Registry。
- 现有 Terminal v1 HTTP/WebSocket 契约在至少一个发布周期内继续工作。
- v2 Terminal 可以先复用现有 v1 wire contract；内部模块化不得改变外部行为。
- 新 Observation 和 Log API 没有 v1 兼容负担。
- SSH enrollment 只对声明 `manager-enrollment.v1` 的 Agent 开放；旧 Agent 继续使用 legacy admin token import，UI 明确标记兼容模式。
- 废弃接口必须通过文档和响应 header 给出明确移除版本，不静默删除。

### 21.2 数据库迁移

- 新 migration 创建 `observations` 和 `log_sources`，不得修改或删除现有审计、Terminal、服务状态表。
- Agent migration 创建 `manager_credentials`；Manager migration 创建/扩展 `agents`、`agent_status`、`agent_enrollment_jobs`、`discovery_jobs`、`discovery_results` 和稳定 `manager_id` metadata。
- migration 必须可在现有 Agent SQLite 上原地运行。
- upgrade 前安装流程继续备份数据库。
- migration 失败必须 fail clearly，Agent 不以部分 schema 启动。
- rollback 到不识别新表的旧版本时允许新表保留；旧版本不得读取或修改它们。
- 回滚到不识别 `manager_credentials` 的旧 Agent 后，Manager-specific token 暂时不可用；恢复文档必须要求保留 legacy admin token recovery path，或在回滚前完成兼容凭据切换。
- Manager 首次以新 schema 启动且 `agents` 表为空时，先对全部 YAML Agent 做本地 shape、duplicate endpoint/ID、token file permission 和 Credential Store copy 校验；任何本地错误都回滚本次 import 并阻止切换事实来源。Import 不要求远端在线或支持 v2：本地成功后按原 enabled 值写入 Registry，`instance_id` 可暂为 null、connection status 为 `unknown`、`source=config_import`、`enrollment_method=legacy_admin_token`，再由后台 probe 分批补全 identity/capabilities。离线或旧 Agent 不阻塞其他记录迁移。
- 导入成功后 SQLite Registry 成为唯一运行时事实来源；YAML `agents` 标记 deprecated，不再在每次启动覆盖 Web 变更。
- 导入时 credential 文件复制到 Manager credential directory；源 token 文件不自动删除。新 Web Add 默认不复用该共享 admin token。
- 后续 probe 发现 duplicate `instance_id` 时，相关导入记录标为 `unavailable`/`agent_identity_conflict` 并停止 privileged routing，等待管理员合并或删除；不能静默覆盖其中一条。
- 回滚旧版本不会卸载 Agent，但旧版只能看到原 YAML 中的 Agent；动态添加的 Agent 必须在回滚说明中手工导出/恢复。

### 21.3 分阶段迁移

1. 固化现有 Agent、Control Plane、Terminal 和 Agent-scoped proxy contract tests。
2. 引入 Composition Root 和模块接口，保持现有 API 行为。
3. 创建 Observation/Log 领域模型、Repository 和 Agent SQLite migration。
4. 增加 Local Ingest API、Public Read API、Summary 和 Prometheus 动态导出。
5. 增加 Agent Manager Credential Repository、受保护 enrollment socket/helper、激活/撤销和 contract tests。
6. 创建 SQLite Agent Registry/Credential Store，迁移 YAML registry，并让 v1 API 读取新 Registry。
7. 增加 Manager Enrollment Orchestrator、系统 SSH adapter、普通用户 auto path、systemd CLI/local socket path 和可选 service-key path。
8. 增加 Manager v2 enrollment/add/edit/remove/probe API 和补偿清理。
9. 增加 bounded Discovery jobs 和安全测试。
10. 引入前端 Router、Runtime Capabilities、Query Client 和 App Shell，先保持旧页面可用。
11. 迁移 Fleet/Add/Enrollment/Discovery/Agent Detail，再逐项迁移 Terminal、Services、Observations、Logs、Metrics 和 Audit。
12. 删除 `AppRoutes` view state machine、全局 `AgentContext` 和已经无调用的旧 CSS/API wrapper。
13. 更新普通用户、systemd CLI、受限 service key、trusted-LAN、凭据备份、Discovery、安全、Producer 和回滚文档。

每个阶段必须可独立测试和发布，不允许长期维护一个不可运行的大重写分支。

### 21.4 实施计划拆分

本系统规格定义共同目标，但后续必须写两个独立 implementation plan：

- **Workstream A — Agent foundation：** Composition Root、Observation、Log、SQLite、Summary、Prometheus、Manager Credential/Enrollment helper、Standalone UI 和 PTY 回归。
- **Workstream B — Fleet Console：** SQLite Registry、Credential Store、SSH/CLI Enrollment、Validation、Discovery、Manager proxy、Fleet/Monitoring UI 和前端 feature architecture。

Workstream A 可独立交付 Agent v2；Workstream B 依赖 Agent v2 capabilities/summary/manager-enrollment 契约，但不得反向让 Agent 依赖 Manager。

## 22. 测试策略

### 22.1 Unit tests

- Observation 字段和 `details` 大小/深度校验；
- identity key 的 label canonicalization；
- TTL、fresh/stale 和 cleanup 判定；
- 乱序、幂等和 timestamp conflict；
- Local Ingest 固定 `producer_id=local`，并拒绝请求提供 producer identity；
- Log realpath、allowed roots 和 symlink escape；
- tail 行数、字节数、UTF-8 replacement 和 truncation；
- Prometheus name/label 验证和 series 上限；
- Registry URL normalization、duplicate ID/URL 和原子更新；
- Agent instance ID 首次生成、持久化、格式错误和升级保留；
- Manager ID 首次生成、持久化和恢复；
- Agent Manager credential hash、pending TTL、权限限制、激活、轮换和撤销；
- Credential Store 权限、原子替换和删除；
- Enrollment job 状态、TTL、容量、单次消费、取消和失败补偿；
- SSH argv builder 的 user/host/port 校验、固定 remote helper 和 option injection 拒绝；
- Local enrollment socket 的 peer credential、mode、重复提交和 replay 拒绝；
- Transport profile/scheme/CIDR 匹配和 trusted-LAN 显式警告；
- Discovery Scope 子网、host/port 上限、job 状态和 cleanup；
- Fleet partial summary 和 status transition；
- secret redaction；
- 配置 fail-closed 校验。

### 22.2 Contract tests

- 无认证 Local Ingest Observation/Log create/update/error 响应，以及固定 `producer_id=local`；
- Public Observation list/detail/filter/pagination；
- Log Source upsert/list/detail/tail；
- 统一 v2 error envelope 和 correlation ID；
- `/metrics` fresh/stale 行为；
- Agent enrollment create/status/cancel/consume、legacy validate、create/update/delete/probe/list；
- Legacy v1 `/api/capabilities` 无 `instance_id` 时的 degraded preview、Manager-generated ID、endpoint-only dedupe 和受限 v1 routes；
- Agent pending Manager credential capabilities/summary/activate/revoke 权限；
- SSH enrollment helper v1 stdin/stdout schema、4/8 KiB limits、identity binding、duplicate/replay 和 safe error；
- Fleet Overview 和 `/api/v2/summary`；
- `/api/v2/capabilities` 返回稳定 `instance_id` 和 v1/v2 capability IDs；
- Discovery scopes/jobs/cancel/results；
- Discovery result → enrollment ID binding、source derivation 和 mismatched URL/profile 拒绝；
- Manager Agent-scoped Observation/Log/Service/Terminal proxy；
- v1 Terminal HTTP 和 WebSocket 回归契约。

### 22.3 Integration tests

- SQLite migration、restart 后数据保留和 WAL 并发；
- Producer 写入、Public API 查询和 Prometheus scrape 完整链路；
- 到期后 Public API、tail 和 Prometheus 的一致行为；
- 文件注册后被删除、替换为 symlink 或移出 allowed root；
- 1000 行、960 KiB 内容和 1 MiB wire response tail 边界；
- PTY 创建、输入输出、resize、reconnect、close 和 orphan cleanup；
- Public 与 Ingest listener 的路由/端口隔离，Public listener 不提供任何写入路由；
- 无 `Authorization` 的 loopback Producer 写入成功，Public/Manager bearer token 对 Local Ingest 不产生额外权限；
- Local Ingest 并发上限和 `503 ingest_capacity_exceeded` 恢复；
- Agent restart 后 Terminal 状态 reconciliation 和 Observation 保留；
- YAML Agent 首次导入、离线/旧版记录不阻塞、本地 credential copy 失败全量回滚、duplicate identity 后续隔离，以及 Manager restart 后 Registry/status 恢复；
- Agent credential 写入权限、hash-only persistence、两端 restart 后继续认证、更新失败 rollback 和删除；
- 多 Agent probe concurrency、单 Agent 超时和 Fleet partial result；
- Discovery job progress/cancel/timeout/dedupe/24 小时 cleanup；
- 普通用户 Manager 通过现有 SSH config/`ssh-agent`、不同远端用户名完成 auto enrollment；
- systemd Manager pending enrollment → 用户 CLI SSH → local Unix socket → preview → add 完整链路；
- SSH auth 失败、host key changed、pending 过期和 Add 失败补偿不留下可用 Registry credential；
- Enrollment 在 credential issued、verified、activation requested、remote activated 和 local commit 每个 phase boundary crash 后均能恢复或留下可重试 residual，不丢失撤销所需 token/reference；
- Credential rotation 保留旧连接直到新 token 可用；撤销失败/进程 crash 时保留 old reference 和 residual journal，恢复后最终撤销旧 token；
- 在线删除撤销远端 credential，离线 local-only removal 给出残留状态；
- verified-TLS 与显式 trusted-LAN HTTP/WS 的 API 和 Terminal 链路；
- Discovery → enrollment → Registry → probe → HTTP API proxy 的完整链路；
- 真实 v1 Agent admin token → `/api/capabilities`（无 instance ID）→ degraded preview → verified enrollment ID → save/import 链路，token 不进入响应；
- Manager Terminal ticket 到 Agent Terminal ticket/WebSocket 的完整链路。

### 22.4 Frontend tests

- Runtime mode 正确选择 Agent Terminal 或 Manager Fleet 默认入口；
- URL deep link、back/forward、刷新和 query filter 恢复；
- Fleet 搜索/筛选/排序、partial error、empty state 和 responsive card fallback；
- Add Agent 自动 SSH 成功/失败、CLI waiting/submission、过期/retry、输入变更取消旧 enrollment；
- Verify & Save preview、service-key capability 和 legacy recovery 入口；
- Legacy v1 preview 显示 identity unavailable/degraded 警告，且不能误显示 v2 summary 或 stable instance ID；
- Discovery progress/cancel/results 和 candidate 到 enrollment 的 URL/transport/SSH host 预填；
- Agent 删除确认、`agent_in_use` 恢复、enable/disable/probe；
- Standalone Agent Manager access 只显示安全 metadata，并能确认撤销 credential；
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
- Local Ingest 不信任 forwarded headers，且 Public listener、非 loopback 地址和 control-plane mode 均无法访问 Ingest routes；
- token 不出现在错误、日志、审计和 metrics；
- 超大 `details`、深层 JSON、过多 labels 和超大请求被拒绝；
- Prometheus label cardinality 上限生效；
- 未认证用户不能读取 Observation、Log 或 Terminal；
- 本地无认证 Producer 只能访问独立 Ingest listener 的 Observation/Log write routes，不能通过该 listener 调用 Public 管理、读取或 Terminal API；
- 浏览器响应、query cache 和 WebSocket URL 不包含 Agent token；
- Agent `/healthz`、Discovery、capabilities 和 enrollment response 不返回用户公钥、`authorized_keys` 或 SSH key path；
- Manager 不枚举/读取/上传个人私钥，不使用 `shell=True`；恶意 user/host/port/option 或 SSH config 中的 HostName/ProxyCommand/ProxyJump/LocalCommand 不能改变 validated effective target 或注入命令，`ssh -G` 与实际连接使用同一固定 overrides；
- enrollment token 不出现在浏览器、URL、storage、query cache、进程参数、日志或审计；
- 未授权 SSH key 不能 enrollment；password/keyboard-interactive auth 被禁用；trusted-LAN 首次 key 可 accept-new，verified-TLS auto 的未知 key 必须转 CLI，CLI 可人工确认，service-key 必须 strict known_hosts/Host CA；所有模式已记录 key 改变均失败；
- enrollment socket 未授权 peer、过期 ID、重复提交和 replay 被拒绝；
- 动态 Agent URL 和 DNS 解析结果必须位于 allowed CIDR，redirect 和 DNS rebinding 被拒绝；
- Discovery 不能超出配置 Scope、host/port/concurrency/time 限制；
- Candidate fingerprint 不能绕过 SSH enrollment/Manager-token authorization；
- trusted-LAN HTTP 只有本地 profile 和 Agent listener 同时启用、目标位于 allowed CIDR 时可用；其他 HTTP endpoint fail closed；
- 普通内网主机没有有效 token 时不能读取数据、创建 Terminal ticket 或 attach WebSocket；
- 可选 service Ed25519 key 的部署检查确认 forced command、禁 PTY 和禁 forwarding，不能启动任意 Shell；
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
10. Public 和 Ingest listener 使用不同端口和路由集合；Local Ingest 只绑定 loopback、无需 token，并且不能创建 Terminal、读取数据或调用管理 API。
11. 所有 v2 错误符合统一 envelope，并包含 correlation ID。
12. Auth、Terminal、Observations、Logs、Metrics、Audit 和 Storage 可以分别使用内存依赖运行单元测试。
13. 完整后端测试、frontend test/build 和 lint 全部通过。
14. 安装、升级和 rollback 文档覆盖新增端口、token、数据库表和配置。
15. Standalone Agent 登录后直接进入本机 Terminal，且不加载/显示 Fleet selector。
16. Manager 登录后 `/fleet` 能列出 SQLite Registry 中全部 Agent，包括 URL、status、version、Observation 和 Service summary。
17. 用户可以通过 Web 完成 connection → SSH enrollment/CLI waiting → preview → add；SSH 未授权、enrollment 过期、错误 API version 或重复 ID/URL 不得写入可用 Registry credential。
18. Agent instance ID 在重启和升级后保持不变；重复 instance ID 或 normalized endpoint 均不能注册。
19. 修改 URL/transport 使用现有 credential 验证相同 identity；credential rotation 失败时旧配置和连接继续可用。
20. 在线删除 Agent 撤销远端 Manager credential 并移除本地 credential；离线 local-only removal 必须明确确认且不影响远端 Agent 数据；活跃 Terminal 时删除被安全阻止。
21. Discovery 只能扫描命名 Scope，显示可取消进度，并把 Candidate 带入逐个 SSH enrollment，不从 fingerprint 直接注册。
22. 浏览器网络请求、storage、URL 和渲染内容中均不存在 Agent token、SSH output、private key path 或 `SSH_AUTH_SOCK`。
23. Fleet 中单个 Agent 离线时其余 Agent 和 last-known summaries 仍正常显示。
24. `/agents/:agentId/...` 可深链接、刷新和前进/后退；Terminal/请求状态不跨 Agent 泄漏。
25. Fleet、Add、Discovery、Agent Detail 和 Audit 满足 WCAG 2.2 AA 关键交互要求，并通过 375–1440 px 布局验证。
26. `AppRoutes` view state machine 和承担 Fleet 全局状态的 `AgentContext` 被目标路由/query 架构替代。
27. 普通用户 Manager 可以使用现有 SSH config/`ssh-agent`，以不同远端用户名添加 Agent；全过程不创建远端专用用户，也不要求 NIS 或相同 UID。
28. systemd Manager 可以通过短期 enrollment ID、当前用户 CLI 和受保护 Unix socket 完成同一添加流程，Manager daemon 不读取个人私钥。
29. Agent 只保存 Manager token hash；Manager 只在 owner-only Credential Store 保存明文；两端重启后已激活 credential 继续有效。
30. trusted-LAN HTTP/WS 只有双方本地显式启用且目标位于 private allowlist 时可用，并在 UI 持续显示未加密警告；普通内网主机无 token 时仍被拒绝。
31. 可选 Manager service Ed25519 key 只允许执行 enrollment forced command，不能创建 SSH PTY、任意 Shell 或端口转发。
32. Enrollment/rotation 在任何远端 issuance/activation 与本地 commit 边界崩溃后都能从 durable journal 恢复、完成撤销或显示可重试 residual，不遗失唯一 token/reference。
33. Discovery result 只有在 URL、IP、port 和 transport profile 全部匹配时才能绑定 enrollment；注册后的 `source=discovery` 由服务端关系产生，浏览器不能伪造。
34. Agent 主机任一本地用户都可以通过 loopback Local Ingest 无 token 写入；所有记录的 `producer_id` 固定为 `local`，请求不得伪造 producer identity。

## 24. 风险与缓解

### 24.1 Web Shell 等价于远程主机权限

风险：管理员 token 泄露可能导致远程执行任意命令；运行账号具有 sudo 时影响可达到 root。

缓解：默认 HTTPS、严格 token 文件权限、限流、短期 Terminal ticket、安全审计、最小 sudoers 和默认 loopback bind。trusted-LAN HTTP 部署必须明确承担传输机密性风险。部署文档不能把 bearer token 描述为低风险只读凭据。

### 24.2 任意 `details` 导致存储膨胀或泄密

缓解：16 KiB 限制、深度限制、请求体限制、过期清理、禁止自动导出、Producer 指南和 secret redaction 测试。

### 24.3 Log tail 形成任意文件读取

缓解：本地注册、稳定 Log ID、allowed roots、realpath 双重校验、普通文件限制、tail 限制和安全审计。

### 24.4 动态 labels 导致 Prometheus 高基数

缓解：label 数量/长度限制、series 总上限、拒绝新 identity、禁止 path/message/details 成为 label。

### 24.5 SQLite 写入竞争

缓解：最新值模型、短事务、WAL、busy timeout、批量后台清理以及不在数据库事务中读取日志文件。初始版本不增加 batch ingest；只有实测证明需要时才扩展。

### 24.6 动态 Agent 凭据泄露

风险：SSH enrollment 会让 Manager 持有具备 Terminal/管理能力的 Manager-specific Agent token。

缓解：token 只经 SSH 或受保护本地 socket 进入 Manager，永不经过浏览器；Agent 只保存 hash，Manager 使用独立 `0600` credential file，SQLite 只保存 reference，响应/审计脱敏，并支持独立轮换和撤销。Durable enrollment/rotation journal 在远端激活前保存 credential/reference，phase-boundary crash 后恢复或显式报告 residual，不能由 orphan cleanup 提前删除。备份文档必须明确 credential directory 和 journal 必须成组恢复。

### 24.7 Discovery 退化成通用端口扫描器或 SSRF

风险：任意网络/端口输入可被滥用于探测内网服务。

缓解：只允许本地命名 Scope、private CIDR、固定 endpoint/transport profile、bounded concurrency/time、精确 fingerprint、无 redirect、DNS 结果复检，并要求用户对单个 Candidate 显式执行 SSH enrollment 或 legacy credential 验证。

### 24.8 Fleet polling 放大负载

风险：Agent 数量增加时，Manager 频繁 fan-out 可能产生连接风暴。

缓解：summary probe 独立于详情、bounded semaphore、jitter、15 秒默认周期、45 秒 stale window、标签页隐藏时前端停止额外 polling、详细数据按需加载。

### 24.9 Agent-only 与 Manager UI 行为分叉

风险：维护两套页面会重复逻辑并产生不一致。

缓解：共享 feature 与 App Shell，Runtime Capabilities 只决定路由入口和可见功能；禁止复制 Terminal/Services/Observations 组件。

### 24.10 SSH enrollment 扩大命令执行面

风险：若 Web 输入可以控制 SSH option、identity path、远端命令或 shell quoting，Manager 可能成为本地/远端命令执行入口；若 helper 权限过宽，service key 可能获得完整 Shell。

缓解：使用系统 OpenSSH 但只通过固定 argv builder 调用；严格验证 user/host/port，禁止 `shell=True` 和浏览器 option；以 validated IP/User/Port command-line overrides、禁 ProxyCommand/ProxyJump/LocalCommand 和 `ssh -G` effective-config check 固定目标。远端 helper 使用 versioned stdin/stdout 协议和有界输出。Service key 必须使用 forced command、禁 PTY/forwarding，并在启用前做 Agent 端部署检查。

### 24.11 Local enrollment socket 被冒用

风险：systemd Manager 的本地 socket 若权限过宽，其他本机用户可能提交伪造结果、抢占 enrollment ID 或 replay 旧结果。

缓解：socket parent/owner/group/mode fail closed，验证 Unix peer credentials；job 使用短 TTL、单次状态转换、目标绑定、容量限制和 replay 拒绝。Socket payload、token 和完整 CLI command 不进入日志或审计。

### 24.12 Trusted-LAN HTTP 泄露高权限数据

风险：HTTP/WS 不提供机密性或服务端密码学身份；若内网存在抓包、ARP/DNS 欺骗、恶意网关或边界误配置，Agent token、Terminal 内容和日志可能泄露。

缓解：该 profile 默认关闭，只允许本地配置的 private CIDR，Manager 与 Agent 双方都必须显式启用，UI 持续显示未加密警告。任何不满足“无 Agent 冒充、无主动中间人、无被动抓包”假设的网络必须使用 verified TLS；不能在 UI 中一键忽略 TLS。

### 24.13 本地进程伪造或淹没 Observation

风险：Local Ingest 只依赖 loopback 且没有 token，因此 Agent 主机上的任意用户、被攻陷进程或本地代理都可以创建/覆盖 Observation、注册允许根目录内的 Log Source，或通过大量请求消耗资源。`producer_id=local` 不能提供归属或追责。

缓解：这是本规格明确接受的单机信任模型。Ingest listener 只能绑定 `127.0.0.1`/`::1`，不出现在 Public/Manager listener，不信任 forwarded headers；请求体、字段、identity/series、并发和 SQLite 写入均有界。部署文档必须禁止端口转发/反向代理暴露 8766；若未来需要本机用户隔离，应单独设计 Unix socket owner/group policy，而不是重新加入共享 token。

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
14. 默认使用一次性 SSH enrollment 创建 Manager-specific Agent token；Agent 只保存 hash，Manager 把明文存入独立 `0600` 文件，浏览器永远看不到 token。
15. Discovery 只扫描管理员本地配置的命名 Scope；Candidate 代表真实 Agent 但尚未授权给当前 Manager，必须逐个 enrollment 后添加。
16. Fleet 页面使用 data-dense table + drill-down；Agent 详情使用 URL 和二级页签，不使用全局 Agent selector 代替导航。
17. 前端使用 React Router + TanStack Query + feature ownership，不增加 Redux 或大型 UI framework。
18. 视觉采用中性、数据密集、无障碍优先的企业工具风格，不采用荧光终端主题或装饰性 dashboard。
19. SSH 只用于 enrollment，不作为长期 HTTP、Terminal WebSocket 或 Prometheus tunnel。
20. Manager 普通用户模式自动尝试系统 SSH；systemd 模式使用当前用户 CLI 和受保护本地 Unix socket；专用受限 Ed25519 service key 是可选无人值守路径。
21. OpenSSH 自己选择并验证客户端 key；Agent 不返回 `authorized_keys`/用户公钥，Manager 不读取或匹配个人私钥文件。
22. Manager 与 Agent SSH 用户可以不同；系统不创建专用远端用户，也不要求相同 UID、NIS、LDAP 或统一用户目录。
23. 默认 transport 是 verified TLS；显式 trusted-LAN HTTP/WS profile 仅用于满足无 Agent 冒充、无主动中间人和无被动抓包假设的私有网络，并持续显示未加密警告。
24. 普通内网主机即使能够访问 Agent 端口，也必须持有有效 token 才能读取 Agent 数据、创建 Terminal 或执行管理操作。
25. Enrollment 和 credential rotation 使用 durable journal；远端 credential 已签发/激活后，本地 token/reference 在完成 Registry commit 或确认撤销前不得删除。
26. Local Producer 通过独立 loopback HTTP listener 无 token 写入；系统信任本机全部用户/进程，`producer_id` 固定为 `local`，不提供 per-producer 身份或权限。
