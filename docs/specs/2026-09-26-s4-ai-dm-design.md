---
spec_id: s4-ai-dm-design
version: 1.1.0
status: frozen
proposed_at: 2026-09-27
frozen_at: 2026-09-27
owner: architecture
depends_on:
  - product-constitution@1.1.0
  - system-design@1.1.0
  - s2-realtime-interface-design@1.2.0
  - s3-frontend-constitution@1.1.0
  - s4-ai-dm-constitution@1.1.0
  - verification-matrix@1.5.0
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
supersedes: s4-ai-dm-design@1.0.0
---

# S4 AI DM 设计 v1.1

## 1. 状态与效力

本文件是 S4 AI DM 的活动设计规格，状态为 `frozen`。它把 S4
`template-only` 决策落实为可验证的模板链路。

v1.0.0 的 provider、LLM、ContextBuilder、OutputGate、真实 provider
性能和 cold/warm 指标条款已 deferred。它们可以保留在本文末的“未来
provider 重新进入附录”中，但不能定义当前 schema、测试、指标或完成条件。

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

### 4.1 TemplateIntent

```python
class TemplateIntent(StrictModel):
    intent_id: UUID
    source_event_id: UUID
    source_revision: int
    source_phase: Phase
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


class TemplateCatalog(StrictModel):
    catalog_version: str
    variants: dict[str, TemplateVariant]
```

所有模板文本在代码中静态定义。Catalog 校验必须拒绝重复 ID、未知
placeholder、未声明 fact kind、同形字/confusable、零宽字符和双向控制字符。

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
    channel: Literal["public", "seat"]
    audience_bindings: list[TemplateAudience]
    source: Literal["template"] = "template"
```

`source_event_ids` 与 facts 一一对应、排序且无重复。`final_text` 只来自
服务端模板和 allowlisted fact 字段。

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
    ]
    elapsed_ms: int
```

没有 provider/model/raw output/prompt 字段。`suppress_reason` 只在
`suppressed` 时非空。

### 4.8 Announcement Slot 与 Admission

```python
class DMAnnouncementSlot(StrictModel):
    domain_seq: int
    room_id: UUID
    revision: int


class DMAdmissionResult(StrictModel):
    domain_seq: int
    transport_seq: int
    admitted: bool
    message: DMTemplateMessage | None
```

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

路由表硬编码。玩家正常发言不产生 intent；player speech 不进入 template
facts；任何 intent 都不能产生 `llm_eligible`。

## 6. Template Registry

### 6.1 版本

- `catalog_version` 是冻结字符串，并进入 trace。
- 同一 catalog version 的文本不可原地变化。
- 文本变更必须提升版本并更新验证证据。

### 6.2 Variant 选择

Variant key 固定为：

```text
(category, channel, style, audience_bindings, source_event_id)
```

`TemplateRegistry.select_by_key(key) -> TemplateVariant` 只接受完整五元组，
返回 catalog 中唯一的 variant；`template_variant_id` 是输出字段，不参与
选择键。

同一 key 必须稳定选择同一 variant。Variant 选择不得读取真实时间、全局
随机数、环境变量、数据库或 provider。

### 6.3 模板覆盖

所有 route table category、public/seat、三种 style 都必须有至少一个合法
variant；测试要检查未知 category、缺失 variant 和重复 ID 失败关闭。

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
  -> final_text
```

不追加动态前缀或后缀。未替换 placeholder、额外 placeholder、空最终文本
或 fact 不匹配都抛出 `TemplateRenderError`。

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
slot。不得合并或新增第三套序列。

### 9.2 Admission

接纳前必须原子复检：

1. slot `domain_seq` 未处理；
2. `slot.revision == state.revision`；
3. slot 对应的 phase/event 仍是最新；
4. room 未关闭且未过期；
5. message audience 仍与当前 session binding 匹配。

失败时记录 suppressed trace，不发布消息，也不创建网络调用。

### 9.3 顺序

`game.ended` 不能越过更早未完成 `dm.message`。模板消息在 actor 顺序内
立即解析并接纳，不等待网络、provider 或外部任务。

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

公式：

```text
completed = template_admitted + render_failed + slot_suppressed
template_admission_rate = template_admitted / completed
render_failure_rate = render_failed / completed
slot_suppressed_rate = slot_suppressed / completed
```

没有 provider hit/fallback/cold/warm 指标。

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
| Unicode 控制字符 | `TemplateRenderError` | 否 |
| stale revision/phase/room | suppressed trace | 否 |
| duplicate slot | suppressed trace | 否 |
| 正常模板解析 | resolved message | 是 |

不得自动创建网络请求作为任何失败的处理。

## 13. 测试策略

### 13.1 单元

- contract strictness；
- intent 路由完整性；
- catalog 覆盖、Unicode 和确定性；
- renderer 事实绑定、claim 隔离和失败关闭；
- service 无 I/O、确定性和错误；
- metrics 终态守恒。

### 13.2 集成

- domain/transport 双序列；
- `game.ended` 顺序；
- duplicate/stale slot；
- public/seat/audit/replay 隐私。

### 13.3 浏览器

- 六玩家真实模板流程；
- stage 只显示 public DM；
- seat 只显示本 seat 授权 DM；
- 暂停、重连和终局顺序；
- 无 provider 网络。

### 13.4 性能

- `LAT-001`: template enqueue p95 `< 200ms`；
- `LAT-004`: six-client broadcast p95 `< 300ms`；
- `LAT-005`: fail-closed path `< 500ms`；
- active admission absolute ceiling `<= 2.0s`。

## 14. 验收与版本

当前 S4 完成定义只包括模板 active rows。`S4-PROV-*`、`LAT-002/003`、
provider schema/refusal/truncation 和 raw prompt/output 测试全部 deferred。

四路独立复核：

1. state/outbox；
2. 信息隔离；
3. template registry/renderer；
4. determinism/latency/E2E。

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
