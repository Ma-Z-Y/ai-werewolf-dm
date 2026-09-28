---
spec_id: s4-ai-dm-design
version: 1.1.5
status: frozen
proposed_at: 2026-09-27
frozen_at: 2026-09-28
owner: architecture
depends_on:
  - product-constitution@1.1.0
  - system-design@1.1.0
  - s2-realtime-interface-design@1.2.0
  - s3-frontend-constitution@1.1.0
  - s4-ai-dm-constitution@1.1.0
  - verification-matrix@1.5.5
overrides:
  - target: system-design@1.1.0#4
    scope: S4 dm_trace is clipped to template metadata and no raw diagnostics leave memory.
  - target: system-design@1.1.0#7.2.1
    scope: S4 active context construction is template-only; provider context is deferred.
  - target: system-design@1.1.0#7.3
    scope: S4 active output is server-owned template text, not model output.
  - target: system-design@1.1.0#7.4
    scope: S4 validates template facts, Unicode, audience, and source-event mapping.
  - target: system-design@1.1.0#7.7
    scope: S4 active admission is template-only; 2.0s remains the absolute ceiling.
  - target: system-design@1.1.0#12
    scope: S4 host audit exports clipped template trace only.
supersedes: s4-ai-dm-design@1.1.4
---

# S4 AI DM 设计 v1.1.5

## 1. 状态与效力

本文件是 S4 AI DM 的活动设计规格，当前状态为 `frozen`。它把 S4
`template-only` 决策落实为可验证的模板链路。

v1.0.0 的 provider、LLM、ContextBuilder、OutputGate、真实 provider
性能和 cold/warm 指标条款已 deferred。它们可以保留在本文末的“未来
provider 重新进入附录”中，但不能定义当前 schema、测试、指标或完成条件。

v1.1.1 增加 canonical variant key、unused-fact 检测、admission 绝对
deadline、双序列守恒指标、模板废弃预留字段和更严格的浏览器验收。
v1.1.2 将“纯 SHA-256 digest 查询”修正为结构化
`TemplateVariantKey` 查询；digest 改为独立纯函数，只用于 trace、
日志关联和跨实例校验，不参与反向选择。
v1.1.3 统一 DM transport 序列：`RoomActor.outbox_seq` 是唯一 wire 序列，
`domain_to_transport` 保存实际 wire 值；非 DM 帧可产生数值间隙，但所有
值必须唯一、严格递增且不回归。admission 后的投递失败只记录独立
`transport_failed` trace，不撤销 admission，也不阻塞后续 domain slot。

本文件冻结设计边界，但不授权实现。implementation plan、provider 和任何
S4 代码仍需明确授权。

## 2. 目标与非目标

### 2.1 目标

当前 S4 只在服务端确定性路径生成 DM 消息：

```text
已提交领域事件
  -> 确定性 TemplateIntentSelector
  -> frozen TemplateRegistry
  -> allowlisted fact binding
  -> TemplateRenderer
  -> RoomActor admission
  -> 既有 public/seat transport
```

要求：

1. 规则、阶段、可见性、事实、模板文本和接纳顺序由服务端决定。
2. 所有 active DM 消息的 `source` 都是 `template`。
3. 没有 provider job、网络调用、API key、prompt、模型或原始模型输出。
4. 模板失败必须 fail closed，不能继续发布占位或网络结果。
5. 公共、座位和主持人审计继续使用既有授权边界。
6. 相同输入、catalog version、intent 和 audience 产生相同字节结果。

### 2.2 非目标

- 不做 LLM 润色、多轮对话、模型状态判断或 provider 智能路由。
- 不做玩家侧 AI 军师、AI 补位玩家、隐藏身份推理或主动插话。
- 不改变 S1/S2/S3 的规则、协议和权限边界。

## 3. 组件

| 组件 | 责任 | 约束 |
| --- | --- | --- |
| `TemplateIntentSelector` | 从已提交事件和 state 选择 intent/audience，并投影 allowlisted `TemplateFact` | 纯函数、无网络、无 provider；不把 state/event 传给 service |
| `TemplateRegistry` | 提供版本化、冻结的模板和变体 | 无动态生成文本 |
| `TemplateRenderer` | 绑定允许事实并生成最终文本 | fail closed、确定性、Unicode 安全 |
| `TemplateDMService` | 解析 intent、调用 registry/renderer | 同步、无 provider、无 I/O |
| `RoomActor` adapter | 消费 outbox slot、去重和 admission | 保留领域/传输双序列 |
| `DMTemplateMetrics` | 记录模板路径终态和延迟 | 无 provider/cold/warm 字段 |
| Public/seat UI | 渲染已接纳模板消息 | 只消费协议白名单字段 |

不存在 active `LLMProvider`、`ContextBuilder`、`OutputGate`、
`DMJobEnvelope`、`ProviderRenderResult` 或 `DMRenderOutput`。

## 4. 契约

公共类型别名固定为：

```python
TemplateCategory = Literal[
    "PHASE_NOTICE",
    "DEATH_NOTICE",
    "EXILE_NOTICE",
    "NO_EXILE_NOTICE",
    "SEAT_PROMPT",
    "PAUSE_NOTICE",
    "TERMINAL_NOTICE",
]
TemplateChannel = Literal["public", "seat"]
TemplateStyle = Literal["neutral", "formal", "urgent"]
```

### 4.1 TemplateIntent

```python
class TemplateIntent(StrictModel):
    intent_id: UUID
    source_event_id: UUID
    source_revision: int
    source_phase: Phase
    catalog_version: str
    category: Literal[
        "PHASE_NOTICE",
        "DEATH_NOTICE",
        "EXILE_NOTICE",
        "NO_EXILE_NOTICE",
        "SEAT_PROMPT",
        "PAUSE_NOTICE",
        "TERMINAL_NOTICE",
    ]
    channel: Literal["public", "seat"]
    audience_seat_ids: list[int]
    audience_bindings: list[TemplateAudience]
    template_variant_id: str
    style: Literal["neutral", "formal", "urgent"]
    source_event_ids: list[UUID]
    allowed_fact_kinds: frozenset[str]
    source: Literal["template"] = "template"
```

没有 `route`、`llm_eligible` 或 provider 字段。`channel="seat"` 时恰好一个
audience binding；public 时没有座位绑定。

### 4.2 Template Audience

```python
class TemplateAudience(StrictModel):
    seat_id: int | None
    session_id: UUID | None
```

seat audience 必须同时有 `seat_id` 和当前 `session_id`。public audience
两者都为 `None`。

### 4.3 TemplateFact

```python
class TemplateFact(StrictModel):
    fact_id: UUID
    source_event_id: UUID
    visibility: Literal["public", "seat"]
    audience_seat_ids: list[int]
    kind: Literal[
        "phase",
        "player_died",
        "player_exiled",
        "no_exile",
        "vote_summary",
        "seat_prompt",
        "game_ended",
    ]
    fields: dict[str, str | int]
```

`fields` 只允许各 kind 的冻结键。template 不能直接接收 `GameState`、
事件对象、SQLite 对象、token、session 或原始玩家发言。

### 4.4 TemplateCatalog 与 TemplateVariant

```python
class TemplateVariant(StrictModel):
    template_variant_id: str
    category: str
    channel: Literal["public", "seat"]
    style: str
    template_text: str
    allowed_fact_kinds: frozenset[str]
    placeholders: frozenset[str]
    deprecated_at_version: str | None = None


class TemplateCatalog(StrictModel):
    catalog_version: str
    variants: dict[str, TemplateVariant]
    reserved_variant_ids: frozenset[str] = frozenset()
```

所有模板文本在代码中静态定义。Catalog 校验必须拒绝重复 ID、未知
placeholder、未声明 fact kind、同形字/confusable、零宽字符和双向控制字符。

`deprecated_at_version` 和 `reserved_variant_ids` 是 v1.1.1 的未来兼容
预留字段。当前 S4 只要求模型接受并序列化这两个字段，不启用任何
reserved ID 校验、deprecated 回放或新 variant 选择行为；这些语义必须由
未来独立版本化任务定义。

### 4.5 TemplateRenderRequest 与 TemplateRenderResult

```python
class TemplateRenderRequest(StrictModel):
    intent: TemplateIntent
    catalog_version: str
    facts: list[TemplateFact]


class TemplateRenderResult(StrictModel):
    intent_id: UUID
    template_variant_id: str
    catalog_version: str
    final_text: str
    source_event_ids: list[UUID]
    unused_fact_ids: list[UUID]
    channel: Literal["public", "seat"]
    audience_bindings: list[TemplateAudience]
    source: Literal["template"] = "template"
```

`source_event_ids` 排序且无重复。`unused_fact_ids` 记录传入但没有被
variant 使用的所有 fact，同样排序且无重复；若传入非空 facts 且
`unused_fact_ids == all facts`，渲染必须以 `NO_FACTS_USED` 失败。
`final_text` 只来自服务端模板和 allowlisted fact 字段。

### 4.6 DMTemplateMessage

```python
class DMTemplateMessage(StrictModel):
    message_id: UUID
    room_id: UUID
    revision: int
    channel: Literal["public", "seat"]
    audience_bindings: list[TemplateAudience]
    text: str
    source: Literal["template"]
```

传输不包含 trace、prompt、provider、model、raw output 或 unused seat facts。

### 4.7 DMTraceRecord

```python
class DMTraceRecord(StrictModel):
    trace_id: UUID
    intent_id: UUID
    template_variant_id: str
    catalog_version: str
    source_event_ids: list[UUID]
    channel: Literal["public", "seat"]
    audience_seat_ids: list[int]
    admission_status: Literal["admitted", "suppressed", "failed"]
    suppress_reason: Literal[
        "stale_revision",
        "stale_phase",
        "room_closed",
        "duplicate_slot",
        "admission_timeout",
        "transport_failed",
    ] | None = None
    elapsed_ms: int
```

没有 provider/model/raw output/prompt 字段。`suppress_reason` 只在
`suppressed` 时非空；admitted/failed trace 必须使用 `None`。

### 4.8 Announcement Slot 与 Admission

```python
class DMAnnouncementSlot(StrictModel):
    domain_seq: int
    room_id: UUID
    revision: int
    trigger_at_monotonic_ms: int
    admission_deadline_monotonic_ms: int


class DMAdmissionResult(StrictModel):
    domain_seq: int
    transport_seq: int
    admitted: bool
    message: DMTemplateMessage | None
```

构造 slot 时必须验证
`admission_deadline_monotonic_ms == trigger_at_monotonic_ms + 2000`。
admission 时若 `now > admission_deadline_monotonic_ms`，结果以
`admission_timeout` fail closed，不发布消息。

## 5. Intent 路由

所有 active intent 都是模板路径：

| 事件 | 模板类别 | audience |
| --- | --- | --- |
| 阶段开始/结束、天黑/天亮 | `PHASE_NOTICE` | public |
| 夜间死亡公开 | `DEATH_NOTICE` | public |
| 放逐 | `EXILE_NOTICE` | public |
| 无人出局 | `NO_EXILE_NOTICE` | public |
| pick/seer/witch 等个人行动提示 | `SEAT_PROMPT` | seat |
| pause/resume/error recovery | `PAUSE_NOTICE` | public |
| 终局 | `TERMINAL_NOTICE` | public |

路由表硬编码。Selector 对 public route 使用
`(state, event, catalog_version)`；seat prompt 必须额外提供当前
`seat_id`、`session_id` 和权威 `expected_session_id`，且后两者必须相等，
否则 fail closed。玩家正常发言不产生 intent；player speech 不进入
template facts；任何 intent 都不能产生 `llm_eligible`。

## 6. Template Registry

### 6.1 版本

- `catalog_version` 是冻结字符串，并进入 trace。
- 同一 catalog version 的文本不可原地变化。
- 文本变更必须提升版本并更新验证证据。

### 6.2 Variant 选择

Variant descriptor 是查询输入，固定为严格不可变模型：

```python
class TemplateVariantKey(StrictModel):
    catalog_version: str
    category: TemplateCategory
    channel: TemplateChannel
    style: TemplateStyle
    audience_bindings: tuple[TemplateAudience, ...]
    source_event_id: UUID
```

`audience_bindings` 必须满足：public 为空；seat 恰好一个绑定。descriptor
不得包含 `template_variant_id`、digest、provider、LLM 或动态文本。

canonical JSON 固定为：

```json
{
  "catalog_version": "...",
  "category": "...",
  "channel": "public|seat",
  "style": "...",
  "audience_bindings": [
    {"seat_id": 1, "session_id": "uuid-string"}
  ],
  "source_event_id": "uuid-string"
}
```

`audience_bindings` 先按 `(seat_id, session_id)` 排序，对象键排序，紧凑
分隔符编码为 UTF-8，再由纯函数
`template_variant_digest(key: TemplateVariantKey) -> str` 计算 lowercase
SHA-256 hex digest。digest 只用于 trace、日志关联和跨实例一致性校验，
不是查询输入，也不能用于反向恢复 descriptor。

`TemplateRegistry.catalog(catalog_version) -> TemplateCatalog` 返回 frozen
catalog；registry 构造后 catalog map 不可变，`select()` 不保留请求历史或
调用状态。`TemplateRegistry.select(key: TemplateVariantKey) ->
TemplateVariant` 是唯一查询入口，先按 descriptor 的 `catalog_version`
定位 frozen catalog，再只使用 route 字段选择唯一 variant。
`TemplateRegistry.resolve(intent) -> TemplateVariant` 是可选便利包装：
先从 intent 构造 descriptor，再调用 `select()`。不提供
`select_by_key(digest)`。

同一 descriptor 必须在任意 registry 实例中稳定选择同一 variant。不同
`source_event_id` 可以生成不同 digest，但只要 route 字段相同，就必须
选择同一静态 variant。Variant 选择不得读取真实时间、全局随机数、环境
变量、数据库、provider 或先前调用历史。

### 6.3 模板覆盖

每个 `(catalog_version, category, channel, style)` 组合只能有一个合法
variant；测试要检查未知 catalog/category、缺失 variant、重复 ID 和
同一 route 多 variant 全部失败关闭。

## 7. TemplateRenderer

活动接口为
`render_template(request: TemplateRenderRequest, catalog: TemplateCatalog)`。

### 7.1 输入校验

渲染前：

1. 校验 catalog version、intent、audience 和 fact visibility；
2. 校验每个 fact kind 在 variant `allowed_fact_kinds` 中；
3. 拒绝未授权 seat facts；
4. 拒绝 claimed/player speech facts；
5. 拒绝隐藏角色、狼队、药水、查验、未公开死因字段；
6. 拒绝零宽字符、双向控制字符和 confusable 模板文本。

### 7.2 渲染

```text
TemplateVariant.template_text
  -> replace only declared placeholders with allowlisted fact fields
  -> record used_fact_ids
  -> unused_fact_ids = supplied_fact_ids - used_fact_ids
  -> final_text
```

不追加动态前缀或后缀。未替换 placeholder、额外 placeholder、空最终文本
或 fact 不匹配都抛出 `TemplateRenderError`。传入非空 facts 但
`used_fact_ids` 为空时抛出 `NO_FACTS_USED`；成功结果必须返回排序后的
`unused_fact_ids`。

### 7.3 隐私

- Public 模板不能读取 seat fact。
- Seat 模板只能读取该 seat 的 fact 和公开 fact。
- Player replay 不返回 `dm_trace`。
- Host audit 只返回 clipped `DMTraceRecord`。

## 8. TemplateDMService

### 8.1 API

```python
class TemplateDMService:
    def resolve(
        self,
        intent: TemplateIntent,
        facts: list[TemplateFact],
        slot: DMAnnouncementSlot,
        catalog_version: str,
    ) -> TemplateRenderResult:
        ...
```

`catalog_version` 必须与 `intent.catalog_version` 相同，否则
`TemplateRenderError(INVALID_CATALOG)`。

### 8.2 行为

- 同步、纯内存、无 I/O。
- 不读取环境变量、网络、密钥、provider 或真实时钟。
- 只调用 `TemplateRegistry` 和 `render_template()`。
- 失败抛出 `TemplateRenderError`，不返回成功结果。
- 成功结果固定 `source="template"`。

## 9. RoomActor 与 Outbox

### 9.1 双序列

- `GameState.outbox[].seq`：领域 announcement slot 顺序。
- `RoomActor.outbox_seq`：客户端 transport 顺序。

RoomActor 维护 `processed_announcement_seq`，按领域 seq 升序消费新增
slot。每个已接纳 DM message 分配并递增一次 `RoomActor.outbox_seq`。
`domain_to_transport` 只保存该实际 wire 值，不另建第三套计数器。
非 DM wire message 可以在两个 DM admission 之间推进同一序列，因此映射
值的子集允许有数值间隙；domain key 必须唯一，transport value 必须严格
递增且不回归。

### 9.2 Admission

接纳前必须原子复检：

1. slot `domain_seq` 未处理；
2. `now <= slot.admission_deadline_monotonic_ms`；
3. `slot.revision == state.revision`；
4. slot 对应的 phase/event 仍是最新；
5. room 未关闭且未过期；
6. message audience 仍与当前 session binding 匹配。

若调用方显式提供 `TemplateIntent`（admission seam），则在任何
offline、duplicate 或 stale 短路之前，必须校验 slot 对应的
`OutboxItem` 存在，且 intent channel/seat audience 与
`OutboxItem.audience_seat_id` 完全一致。缺失 item 使用
`ANNOUNCEMENT_ITEM_NOT_FOUND`，audience 不一致使用
`ANNOUNCEMENT_AUDIENCE_MISMATCH` fail closed；两种失败都不得发布、
建立 `domain_to_transport`、写 trace，或推进 completed/processed
状态。

若 seat-targeted slot 在 admission 时没有当前 actor-owned session，则该
slot 视为 ineligible：RoomActor 只完成 `domain_seq` 并推进
`processed_announcement_seq`，不发布消息、不建立 `domain_to_transport`、
不新增 `DMTraceRecord`、不重试，也不阻塞后续 slot。该行为不同于
stale/duplicate suppression，且不计入模板终态完成指标。

超时使用 `admission_timeout`，其他失败使用已有 stale/duplicate reason。
失败时记录 suppressed trace，不发布消息，也不创建网络调用。

admission 成功后，`dm.message` 通过既有 public/seat subscriber 投递。
若任一符合 audience 的 subscriber `offer()` 返回 `False`，记录独立的
`transport_failed` suppressed trace；该失败不回滚 mapping、不撤销已接纳
消息、不阻塞后续 domain slot，也不改变 room/game state。

### 9.3 顺序

`game.ended` 不能越过更早未完成 `dm.message`。模板消息在 actor 顺序内
立即解析并接纳，不等待网络、provider 或外部任务。接纳后的 wire 投递
与 domain admission 分离：投递失败不改变 `game.ended` 的 domain 顺序。

## 10. 暂停、重连与终局

- 暂停期间不产生新的 DM slot。
- 已解析未接纳的 slot 在 admission 前复检当前 state。
- seat 重连绑定新 session，使旧 seat-targeted 模板消息失效。
- public 模板消息不因 seat 重连失效。
- `GAME_ENDED` 固定模板，并等待更早 slot。
- 终局冻结后不得发布旧 revision 的模板消息。

## 11. 指标与 Trace

### 11.1 模板指标

- `dm_template_eligible_total`
- `dm_template_admitted_total`
- `dm_template_render_failed_total`
- `dm_template_slot_suppressed_total`
- `dm_template_admission_ms` window
- `dm_template_max_domain_transport_lag`
- `dm_template_domain_transport_ratio`

公式：

```text
completed = template_admitted + render_failed + slot_suppressed
template_admission_rate = template_admitted / completed
render_failure_rate = render_failed / completed
slot_suppressed_rate = slot_suppressed / completed
domain_transport_ratio = mapped_domain_slots / completed_domain_slots
max_domain_transport_lag = max(domain_seq - transport_seq, default=0)
```

其中：

- `mapped_domain_slots = template_admitted`；
- `completed_domain_slots = mapped_domain_slots + render_failed +
  slot_suppressed`；
- `completed_domain_slots == 0` 时 `domain_transport_ratio = 1.0`；
- `max_domain_transport_lag` 只在 admitted mapping 上取，仅保留正值；
  mapping 为空或全部 transport 值领先时取 `0`。

映射必须满足 domain key 唯一、transport value 等于实际 wire
`RoomActor.outbox_seq`、按 admission 顺序严格递增且不回归。全局 wire
序列必须从 1 单调连续增长；非 DM 帧可使映射值子集出现间隙。守恒式必须
成立。没有 provider hit/fallback/cold/warm 指标。

### 11.2 Trace 隐私

- 只持久化 `DMTraceRecord`。
- 原始 fact values、玩家 speech、token、session 和完整 state 不写入 trace。
- 本地日志只输出闭集 reason。
- Host audit 只导出 template trace。
- Player replay 不导出 trace。

## 12. 错误与失败关闭矩阵

| 条件 | 结果 | 是否发布 |
| --- | --- | --- |
| catalog version 未找到 | `TemplateRenderError` | 否 |
| variant 缺失或重复 | `TemplateRenderError` | 否 |
| fact 未授权 | `TemplateRenderError` | 否 |
| player claim 作为 fact | `TemplateRenderError` | 否 |
| placeholder 不匹配 | `TemplateRenderError` | 否 |
| 传入 facts 但均未使用 | `TemplateRenderError(NO_FACTS_USED)` | 否 |
| Unicode 控制字符 | `TemplateRenderError` | 否 |
| stale revision/phase/room | suppressed trace | 否 |
| duplicate slot | suppressed trace | 否 |
| now 超过 admission deadline | `admission_timeout` suppressed trace | 否 |
| 已接纳消息投递失败 | `transport_failed` suppressed transport trace | 否（其它匹配 subscriber 仍可收到） |
| 正常模板解析 | resolved message | 是 |

不得自动创建网络请求作为任何失败的处理。

## 13. 测试策略

### 13.1 单元

- contract strictness；
- intent 路由完整性；
- catalog 覆盖、Unicode 和确定性；
- variant descriptor 跨实例确定选择、catalog version 进入 digest、
  audience cardinality 合法、纯 digest 稳定且不可查询、renderer
  事实绑定、unused fact、claim 隔离和失败关闭；
- service 无 I/O、确定性和错误；
- metrics 终态守恒。

### 13.2 集成

- domain/transport 双序列；
- `game.ended` 顺序；
- duplicate/stale slot；
- admission deadline 和 domain/transport 守恒；
- public/seat/audit/replay 隐私。

### 13.3 浏览器

- 六玩家真实模板流程；
- stage 只显示 public DM；
- seat 只显示本 seat 授权 DM；
- 暂停、重连和终局顺序；
- 无 provider 网络。
- 相同固定 room/seed 运行两次时 `dmMessages` deep equal；
- 拦截 provider 域名并断言请求数为 0。

### 13.4 性能

- `LAT-001`: template enqueue p95 `< 200ms`；
- `LAT-004`: six-client broadcast p95 `< 300ms`；
- `LAT-005`: fail-closed path `< 500ms`；
- active admission absolute ceiling `<= 2.0s`。

## 14. 验收与版本

当前 S4 完成定义只包括模板 active rows。`S4-PROV-*`、`LAT-002/003`、
provider schema/refusal/truncation 和 raw prompt/output 测试全部 deferred。

四路独立复核：

1. **State/Outbox:** `game.ended` 顺序、domain seq 单调、全局 wire
   transport seq 连续且无重复、mapping 子集严格递增且守恒；非 DM 帧
   造成的映射数值间隙合法；
2. **Information Isolation:** public 无 seat facts、seat 只读自己、
   player replay 无 `dm_trace`、host audit 仅 clipped trace、玩家声称
   不进入 facts；
3. **Template Registry/Renderer:** 所有 intent 有 variant、confusable
   拒绝、未声明 placeholder 拒绝、未授权 fact 拒绝、缺失模板失败关闭、
   `NO_FACTS_USED` 失败关闭；
4. **Determinism/Latency/E2E:** 字节级相同、`LAT-001/004/005` 重跑、
   固定 seed 两次 `dmMessages` deep equal、无 provider 网络请求。

任何 scope 为 BLOCK 时不得冻结实现交付。

## 15. 未来 Provider 重新进入附录

以下内容是 deferred 扩展，不是当前 S4 实现清单：

- OpenAI-compatible 单 provider；
- 非流式、单请求、public-only；
- 1.5 秒取消，1.6 秒模板 fallback，2.0 秒 admission；
- provider result 只作为不可信 raw output；
- 输出门、短语 allowlist、strict schema；
- provider metrics、cold/warm 和 runtime hit/fallback；
- fake provider、真实 provider 和 provider failure E2E。

重新进入必须：

1. 新建独立 `S4-RE-*` 任务和版本化规格；
2. 重新运行 2 到 3 个候选的 cold/warm spike；
3. 满足 `p99 < 1.2s`、`maximum < 1.5s`、failure `< 5%`、
   spike acceptance `>= 50%`；
4. 冻结 provider/model/region/transport/预算和回滚开关；
5. 补齐 public-only、隐私、失败回退、reconnect/terminal invalidation 和
   真实 DMService 证据；
6. 独立复核并由用户接受后，才能在默认关闭开关下启用。

降低门禁必须提升产品规格版本并获得用户确认。

## 16. 变更历史

### v1.1.5 - 2026-09-28

- 将显式 intent admission 的一致性校验提升为规范性 admission 条款：
  缺失 outbox item 或 intent/outbox audience 不一致时，必须先于
  offline/duplicate/stale 短路 fail closed，且不得发布、建 mapping、
  写 trace 或推进 completed/processed 状态。
- 冻结 `ANNOUNCEMENT_ITEM_NOT_FOUND` 与
  `ANNOUNCEMENT_AUDIENCE_MISMATCH` 的拒绝语义。

### v1.1.4 - 2026-09-28

- 明确 offline seat slot 的 admission 语义：无当前 session 时静默完成
  domain slot，不发布、不建立 transport mapping、不新增 trace、不重试，
  也不阻塞后续 slot；该 ineligible slot 不计入模板终态指标。
- `OutboxItem.audience_seat_id` 仅允许 `dm.message`，并在显式 intent
  admission 时要求 seat intent 与 outbox audience 完全一致，否则
  `ANNOUNCEMENT_AUDIENCE_MISMATCH` fail closed。

### v1.1.3 - 2026-09-27

- 统一 `client_outbox_seq` 与 wire `RoomActor.outbox_seq`，删除私有
  transport 计数器语义。
- 规定 `domain_to_transport` 保存实际 wire 值，允许非 DM 帧造成数值
  间隙但禁止回归或重复。
- 增加 `transport_failed` 投递失败 trace，区分 delivery failure 与
  admission suppression；投递失败不回滚 domain/mapping。
- 增加独立 `S4-04A` transport 交接任务与永久回归要求。

### v1.1.2 - 2026-09-27

- 修正 v1.1.1 中不可实现的 digest-only variant 查询：纯 SHA-256 无法
  从 digest 反推包含 `source_event_id` 和 audience 的 route。
- 增加结构化 `TemplateVariantKey`，并将
  `TemplateRegistry.select(key)` 设为唯一 variant 查询入口。
- 将 `catalog_version` 纳入 descriptor 与 digest，消除无版本查询歧义。
- 将 `catalog_version` 纳入 `TemplateIntent`，并写清 seat selector 的
  `seat_id/session_id/expected_session_id` fail-closed 接口。
- 将 `template_variant_digest(key)` 定义为无状态纯函数，仅用于 trace、
  日志关联和跨实例一致性校验。
- 删除 `select_by_key(digest)`；跨实例选择与 digest 一致性改为永久回归。

### v1.1.1 - 2026-09-27

- 增加 canonical JSON + SHA-256 `template_variant_key`，并规定
  audience_bindings 顺序无关。
- 增加 `unused_fact_ids` 与 `NO_FACTS_USED` fail-closed。
- 增加 `trigger_at_monotonic_ms` /
  `admission_deadline_monotonic_ms` 和 `admission_timeout`。
- 增加 domain/transport 守恒指标 `max_domain_transport_lag` 和
  `domain_transport_ratio`。
- 增加 provider 域名请求为零和固定 seed 字节级 E2E 断言。
- 预留 `deprecated_at_version` 与 `reserved_variant_ids`，当前不启用
  废弃行为。

### v1.1.0 - 2026-09-27

- 将 active 设计收窄为 template-only。
- 删除 active provider calls、prompt、OutputGate、ContextBuilder 和
  provider metrics。
- 增加 deterministic template registry、renderer、fail-closed admission、
  template-only metrics 和 deferred provider appendix。
- 将 1.5s/1.6s/2.0s/50% 从当前运行预算改为未来 provider 门禁。

### v1.0.0 - 2026-09-26

- 冻结 provider-neutral、模板优先、可选 LLM 润色、ContextBuilder、
  OutputGate、DMService、trace 和延迟设计。
