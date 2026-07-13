# Feature Specification: Fleet Overview & Multi-Host Management UI

**Feature**: `003-fleet-overview-ui`

**Created**: 2026-06-15

**Status**: Draft (待评审)

**Depends on**: [001 Linux Host Agent](../001-linux-host-agent/spec.md) · [002 Multi-Agent Control Plane](../002-multi-agent-control-plane/spec.md)

---

## 1. 背景与目标

`002` 已经在后端建好了完整的 multi-agent control plane：一个浏览器入口可以通过 gateway 路由到多个 host agent，并按 agent 维度提供 services / terminals / monitoring / audit 接口。但**前端目前只实现了「单选 active agent」模型**：

- `AgentSelector` 是一个下拉框，一次只能选一个 active agent。
- `HostOverviewPage` 只显示**当前选中那一个** agent 的 `readyz` 状态（一行文字）。
- Terminal / Services / Metrics / Audit 页面全部绑定到这一个 active agent。

用户无法在一个视图里看到「所有 host 的整体状态」，也无法从总览里快速选择、运维、配置某台 host。

**本 feature 的目标**：把前端从「单选 agent」升级为「fleet（多 host）总览 + 下钻管理」体验。

- **Overview** 一屏看到所有 hosts 的状态（可用性、能力、关键健康/指标摘要），支持筛选、排序、搜索。
- 从 Overview 选择任意一台 host，下钻进入该 host 的 terminals / services / metrics / audit。
- 在 Overview 与 host 详情里执行**运维操作**：enable/disable 路由、手动 probe/刷新、以及对该 host 的 service 执行 start/stop/restart。
- 复用 `002` 既有的 per-agent API 与安全模型；只新增**少量后端聚合接口**，不引入浏览器侧凭据管理。

### 范围（已与负责人确认）

- **Scope**：前端为主 + 少量后端聚合。
- **管理深度**：查看 + 选择 + 运维操作（enable/disable、probe/刷新、service start/stop/restart）。**不**在 UI 内新增/删除 host 或编辑连接凭据。
- **位置**：新建 `specs/003-fleet-overview-ui/`，作为 `002` 之上的前端 feature。

### 不改变的前提

- `001` 单 host agent 与 `002` 的契约、安全保证保持不变；本 feature 不修改既有 per-agent API 的语义。
- host 注册表与凭据仍由配置文件驱动（`002` FR-007 / FR-009 / FR-011）。**浏览器永不持有 agent 凭据或上游 terminal ticket。**

---

## 2. 与现状的差距（Gap）

| 维度 | 现状 | 目标 |
|---|---|---|
| 总览 | 只显示 active agent 的 `readyz` 一行 | 所有 host 的状态卡片墙 / 列表，含可用性、能力、健康/指标摘要 |
| 选择 | 顶部单选下拉框 | 从总览卡片直接选择并下钻；下拉框保留为快速切换 |
| 运维 | 无 | enable/disable、手动 probe/刷新、service start/stop/restart |
| 状态获取 | 前端逐个调 `readyz` | 一次性聚合接口返回所有 host 的状态快照（有界、可缓存） |
| 配置可见性 | 无 | 只读展示每台 host 的关键配置（base URL 显示标识、能力、enabled） |
| 多 host 隔离 | 已有 generation 防串台机制（单 agent） | 在多卡片并发刷新下保持隔离，不串台 |

---

## 3. 用户场景与验收（User Scenarios）

### US1 — 一屏总览所有 hosts（P1）

管理员登录后，第一屏即看到所有已配置 host 的状态总览：每台 host 一张卡片/一行，显示名称、可用性状态（`ready` / `degraded` / `unavailable` / `disabled` / `unknown`）、支持的能力、最近一次观测时间，以及关键健康/指标摘要（如 CPU/内存/负载，若该 host 支持 monitoring 能力）。

**独立测试**：配置 3 台 host，停掉其中 1 台。登录后总览应同时显示 3 张卡片，停掉的那台显示 `unavailable` 且不阻塞其它卡片渲染。

**验收**：
1. 总览列出所有 enabled 与 disabled 的 host，并清晰区分二者。
2. 某台 host 不可用时，其卡片显示错误/降级状态，其余 host 卡片仍正常展示数据。
3. 每张卡片显示该 host 的 `observed_at` / 新鲜度；超过 `stale_after` 时标记为陈旧或 `unknown`。
4. 总览支持按状态/名称筛选与搜索，并支持排序（按状态、名称、可用性）。
5. 总览在合理节奏下自动刷新（可配置间隔，默认手动 + 周期），并提供手动「刷新全部」。

### US2 — 从总览选择并下钻某台 host（P1）

管理员从总览点选一台 host，进入该 host 的详情/工作区，可访问其 terminals、services、metrics、audit；当前选中的 host 身份在所有下钻页面里清晰可见。

**独立测试**：在总览选 A，进入 services 后返回总览选 B，确认所有下钻页面切换到 B 且不显示 A 的残留数据。

**验收**：
1. 选择 host 即设置 active agent；下钻页面（terminal/services/metrics/audit）随之重新加载并显示该 host 身份。
2. 仅当该 host `ready` 且具备对应能力时，相应入口才可用；否则入口禁用并给出原因（不可用 / 缺能力）。
3. 快速切换 host 时，旧 host 的延迟响应不能覆盖新 host 的界面（沿用 generation/AbortController 机制，扩展到所有 host-scoped 状态，见 `002` FR-028）。
4. 存储的 active host 不存在时，按 `002` US1.3 规则回退选择（首个 ready → 首个 enabled → 无）。

### US3 — 在总览/详情执行运维操作（P1）

管理员可对 host 执行：手动 probe/刷新单台、enable/disable 路由；并可对所选 host 的某个 service 执行 start/stop/restart。

**独立测试**：对一台 host 点击 disable，确认其卡片转为 `disabled` 且其下钻入口被禁用、不再被聚合 probe；重新 enable 后恢复。对一个无害 service 执行 stop 再 start，确认目标 host 与审计记录均反映该操作。

**验收**：
1. 「probe / 刷新」仅刷新单台 host 的状态，不影响其它卡片。
2. enable/disable 是**路由层开关**（控制是否参与 gateway 路由与 probe），**不触碰凭据**；操作即时反映在卡片与下钻入口可用性上。
3. service 的 start/stop/restart 复用 `002` 的 per-agent service 接口与审计；变更操作**不自动重试**（`002` FR-014）。
4. 任何运维操作的结果（成功/失败/不确定）都有明确 UI 反馈，并对应一条 gateway 审计记录（`002` FR-019）。
5. 对 disabled / unknown host 的运维操作在前端即被禁用或拒绝，不会误发到其它 host。

### US4 — 只读查看每台 host 的配置与能力（P2）

管理员在 host 详情里查看该 host 的只读配置信息：显示用 base URL 标识、API/agent 版本、能力清单、enabled 状态、最近 probe 结果。

**验收**：
1. 仅展示**显示用**标识，绝不展示凭据、原始上游异常文本或私网细节（`002` FR-011 / FR-030）。
2. 能力缺失时，对应 UI 功能保持禁用而非调用（`002` 能力模型）。
3. 配置展示来自权威 agent 注册表，前端不维护第二份机器注册表（`002` FR-023）。

### US5 — 总览级监控摘要（P3）

在每张 host 卡片上展示来自该 host monitoring 能力的轻量快照摘要（如 CPU/内存/负载/关键 service 数量），用于快速判断哪台 host 需要关注。

**验收**：
1. 摘要消费经认证的 JSON snapshot，而非原始 Prometheus 文本（`002` FR-024 类比 / US4.3）。
2. 不引入跨 host 聚合指标、长期存储、告警或 dashboard（`002` FR-029）。
3. 不支持 monitoring 能力的 host 卡片优雅降级（显示「无指标」而非报错）。

---

## 4. 功能需求（Functional Requirements）

### 前端

- **FR-001**：前端 MUST 提供一个 Fleet Overview 视图，一屏展示所有已配置 host 的状态、能力、新鲜度与（可用时）健康/指标摘要。
- **FR-002**：Overview MUST 同时展示 enabled 与 disabled host，并清晰区分；单台不可用 MUST NOT 阻塞其它 host 的渲染。
- **FR-003**：Overview MUST 支持按状态/名称的筛选、搜索与排序。
- **FR-004**：前端 MUST 支持从 Overview 选择任一 host 作为 active host 并下钻其 terminals/services/metrics/audit；active host 身份在所有下钻页面 MUST 清晰可见。
- **FR-005**：所有 host-scoped 的请求与终端状态 MUST 按 host 隔离，并丢弃非当前选择 generation 的响应（沿用并扩展 `002` FR-028）。
- **FR-006**：仅当 host `ready` 且具备对应能力时，前端 MUST 启用相应下钻入口；否则 MUST 禁用并给出原因。
- **FR-007**：前端 MUST 提供单台 host 的手动 probe/刷新与「刷新全部」，并 MAY 提供可配置的周期自动刷新（默认有界、低频）。
- **FR-008**：前端 MUST 支持对 host 的 enable/disable 路由开关，以及对所选 host 的 service start/stop/restart；变更类操作 MUST NOT 自动重试。
- **FR-009**：前端 MUST 对每个运维操作展示成功/失败/不确定（indeterminate）的明确反馈，并复用 `002` 的规范化错误码。
- **FR-010**：前端 MUST 仅展示 host 的显示用标识与非敏感配置；MUST NOT 展示凭据、上游 ticket、原始异常文本或私网细节。
- **FR-011**：前端 MUST 优雅处理空集（无 host / 无 enabled host）与全部不可用的情况。

### 后端（少量聚合新增）

- **FR-012**：后端 SHOULD 新增一个聚合只读接口（建议 `GET /api/fleet/overview`），一次性返回所有 host 的状态快照（id、name、enabled、status、capabilities、`observed_at`、`stale_after`，以及可选的轻量 monitoring 摘要）。该接口 MUST 有界、可缓存，单台 host 失败 MUST NOT 使整个响应失败（按 host 返回各自 status/错误）。
- **FR-013**：后端 SHOULD 新增 host 路由开关接口（建议 `POST /api/agents/{agent_id}/enabled`，body `{enabled: bool}`），作为**运行时路由层开关**：被 disable 的 host 不参与路由与 probe。该接口 MUST NOT 修改或暴露凭据。其持久化策略（仅运行时 vs 写回配置）MUST 在 plan 阶段明确（见开放问题 OQ-1）。
- **FR-014**：聚合接口与开关接口 MUST 沿用 `002` 的鉴权（authenticated `local-admin`）、审计（每个特权请求一条 gateway 审计记录）与错误模型；MUST NOT 引入新的凭据暴露面。
- **FR-015**：聚合接口 MUST NOT 引入跨 host 指标聚合、长期存储、告警或 dashboard；monitoring 摘要仅为单台 host snapshot 的轻量子集。

---

## 5. 信息架构与 UI 结构（建议）

顶层导航从「单一 active agent 的标签页」改为两级：

```
Fleet Overview  (默认首页, 所有 host 总览)
   └── Host: <name>  (下钻工作区, 绑定 active host)
          ├── Terminal
          ├── Services
          ├── Metrics
          └── Audit
```

**Overview 页**：host 卡片墙（或可切换为表格）。每卡：名称、状态徽章、能力图标、`observed_at`/新鲜度、监控摘要、操作区（进入 / probe / enable-disable）。顶部：搜索框、状态筛选、排序、刷新全部、整体计数（如 `3 ready · 1 degraded · 1 disabled`）。

**Host 工作区**：顶部固定显示当前 host 身份 + 快速切换下拉（保留现有 `AgentSelector` 作为快捷切换器），下方为 Terminal/Services/Metrics/Audit 标签页（复用现有页面，仅改为由总览选择驱动）。

### 涉及的前端文件（基于现状）

- 改造：`pages/HostOverviewPage.tsx`（从单 agent 单行 → fleet 卡片墙）。
- 改造：`pages/AppRoutes.tsx`（两级导航 + 总览/工作区切换）。
- 改造/扩展：`agents/AgentContext.tsx`（在「单 active agent」基础上增加 fleet 列表与聚合状态、enable/disable action）。
- 新增：`api/fleet.ts`（封装 `GET /api/fleet/overview`、enable/disable）。
- 新增：`components/fleet/HostCard.tsx`、`FleetFilters.tsx` 等展示组件。
- 复用：现有 `api/agents.ts`、`api/services.ts`、`api/monitoring.ts`、`api/audit.ts`、`api/terminals.ts`。

---

## 6. 数据模型（前端类型，建议）

```ts
type HostStatus = 'ready' | 'degraded' | 'unavailable' | 'disabled' | 'unknown';

type FleetHost = {
  id: string;
  name: string;
  enabled: boolean;
  status: HostStatus;
  capabilities: string[];
  observedAt: string;   // ISO8601
  staleAfter: string;   // ISO8601
  summary?: {           // 可选, 仅当支持 monitoring.snapshot.v1
    cpuPercent?: number;
    memPercent?: number;
    load1?: number;
    serviceCount?: number;
  };
};

type FleetOverview = { hosts: FleetHost[]; collectedAt: string };
```

> 字段直接映射 `002` 的可用性模型与能力清单（`services.v1` / `terminals.v1` / `audit.v1` / `monitoring.snapshot.v1`）。

---

## 7. 安全与约束（复用 002）

- 浏览器先认证后才能取得 host 清单或任何 host-scoped 资源（`002` FR-012）。
- 浏览器**永不**接触 agent 凭据或上游 terminal ticket（`002` FR-011）；本 feature 不新增凭据暴露面。
- enable/disable 是路由开关，**不**等于凭据管理；UI 内不做 host 的增删与凭据编辑（与 `002` non-goal 一致）。
- 变更类操作不自动重试，超时按 indeterminate 呈现（`002` FR-014 / FR-015）。
- 错误响应不泄露凭据、终端内容、私网细节或原始异常文本（`002` FR-030）。

---

## 8. 非目标（Non-Goals）

- 在 UI 内新增/删除 host 或编辑连接配置、凭据。
- host 自动发现、注册或远程安装。
- 跨 host 的批量命令、批量终端执行或协同事务。
- 跨 host 指标聚合 / 长期存储 / 告警 / dashboard / 原始 Prometheus 联邦。
- 多用户 RBAC（首版仍是单一 `local-admin`）。
- agent 切换时保留实时 xterm 缓冲（沿用 `002` 首版限制）。

---

## 9. 成功标准（Success Criteria）

- **SC-001**：配置 N 台 host（含至少 1 台不可用），管理员在一屏总览看到全部状态，不可用 host 不阻塞其它卡片或整页加载。
- **SC-002**：从总览选择任一 ready host 即可正确下钻其 terminals/services/metrics/audit，身份清晰且无其它 host 的残留数据。
- **SC-003**：快速切换 host 时，旧 host 的延迟响应永不覆盖新 host 的界面。
- **SC-004**：disable 一台 host 后，其卡片转 `disabled`、下钻入口禁用、且不再被聚合 probe；re-enable 后恢复。
- **SC-005**：service start/stop/restart 经 per-agent 接口生效，结果（成功/失败/不确定）有明确 UI 反馈且产生 gateway 审计记录；变更不自动重试。
- **SC-006**：所有浏览器可见载荷与日志中，不含任何 agent 凭据或上游 terminal ticket。
- **SC-007**：聚合接口在单台 host 失败时仍返回其余 host 的状态；响应有界、可缓存。
- **SC-008**：`001` / `002` 既有契约测试在本 feature 下继续通过，per-agent API 语义不变。

---

## 10. 开放问题（待评审确认）

- **OQ-1（enable/disable 持久化）**：disable 是仅运行时（重启后按配置文件复位），还是写回配置持久化？写回会触及配置文件写权限与并发，需在 plan 阶段定方案。建议首版**仅运行时**，并在 UI 标注「重启后按配置复位」。
- **OQ-2（自动刷新节奏）**：总览自动刷新默认是否开启？默认间隔多少？是否随 host 数量退避以控制 probe 压力？
- **OQ-3（Overview 默认视图）**：卡片墙 vs 表格作为默认；host 数量大时是否需要分页/虚拟列表。
- **OQ-4（监控摘要字段）**：卡片摘要展示哪些字段，需与 `monitoring.snapshot.v1` 实际返回对齐。
- **OQ-5（聚合接口归属）**：`/api/fleet/overview` 是否仅在 control-plane 模式暴露（与 `002` 的 agent/control-plane 模式划分一致）。

---

## 相关文档

- [001 Linux Host Agent](../001-linux-host-agent/spec.md)
- [002 Multi-Agent Control Plane](../002-multi-agent-control-plane/spec.md) · [architecture](../002-multi-agent-control-plane/architecture.md) · [contracts](../002-multi-agent-control-plane/contracts/)
