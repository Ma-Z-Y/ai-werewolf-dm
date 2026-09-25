---
spec_id: system-design
version: 1.1.0
status: frozen
frozen_at: 2026-09-23
owner: architecture
depends_on:
  - product-constitution@1.1.0
  - rulepack-v1@1.1.0
---

# AI 狼人杀 DM 系统设计 v1.1

## 1. 架构目标

系统必须优先保证：

1. 规则只有一个裁判。
2. 隐藏信息只有一个服务端投影边界。
3. LLM 永远不能直接改变游戏状态。
4. 所有实时操作可幂等、可恢复、可审计。
5. AI DM 超时或格式错误时，游戏仍能继续。
6. 所有状态变化必须通过 `apply_command()` 产生事件，禁止直接修改 `GameState` 字段。

## 2. 部署单元

首版只有两个部署单元：

```text
backend/
  src/werewolf_dm/
    domain/
    application/
    infrastructure/
    interfaces/
  tests/

frontend/

docs/
  specs/

.planning/
```

`domain` 是纯规则核心，不依赖 FastAPI、数据库、WebSocket 或 LLM SDK。

## 3. 模块职责

| 模块 | 职责 | 禁止事项 |
| --- | --- | --- |
| `domain.model` | Pydantic 领域模型、枚举、规则包类型 | 禁止网络和 I/O |
| `domain.state_machine` | 状态转移表、命令合法性、胜负裁定 | 禁止调用 LLM |
| `domain.visibility` | 把真值投影为公共视图和座位视图 | 禁止把全量真值发送给客户端 |
| `application.orchestrator` | 阶段推进、计时器、行动收集、暂停恢复 | 禁止绕过领域命令 |
| `application.dm_service` | 事实模板、LLM 润色、输出门、降级 | 禁止直接修改游戏状态 |
| `application.host_recovery` | 主持人纠错、回滚和校验 | 禁止无审计修改 |
| `infrastructure.persistence` | SQLite、快照、事件、回放 | 禁止成为运行时唯一状态源 |
| `infrastructure.llm` | provider、超时、重试、结构化输出解析 | 禁止把原始输出直接广播 |
| `interfaces.http_ws` | REST 房间接口、WebSocket 命令和推送 | 禁止自行实现规则 |

## 4. 运行时状态与持久化

运行时以 `RoomRuntime` 保存当前状态：

```text
RoomRuntime
  room_id
  rulepack_version
  revision
  game_state
  command_dedupe_cache
  timers
  dm_jobs
  paused
```

持久化包括：

- `room`：房间配置和座位。
- `snapshot`：阶段开始、暂停、纠错前和终局快照。
- `command_log`：幂等命令记录。
- `event_log`：公共和私密审计事件。
- `dm_trace`：模型、提示词版本、允许事实、原始输出、过滤结果和最终输出。

事件日志不是运行时唯一真值，但必须足以完成审计、故障恢复和回放。

### Event Sourcing 边界

- v1.1 使用“内存当前状态 + revision + append-only 事件日志 + 快照”。
- 事件日志用于审计、回放、恢复和调试，不承担每次命令后的全量状态重建。
- 未来只有出现“从任意时间点分叉对局”或“完整事件重建”需求时，才升级为运行时真值来自事件流的完整 Event Sourcing。
- 即使当前不做完整 Event Sourcing，任何领域状态改变仍只能通过返回事件的 `apply_command()` 完成。

## 5. Pydantic 契约

### 5.1 命令信封

所有状态改变都必须经过统一命令信封：

```python
class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

class CommandEnvelope(StrictModel):
    schema_version: Literal["command.v1"] = "command.v1"
    command_id: UUID
    room_id: UUID
    expected_revision: int
    issued_at: datetime
    payload: Annotated[
        PlayerCommand | HostCommand,
        Field(discriminator="command_type"),
    ]
```

规则：

- `command_id` 重复时返回第一次结果。
- `expected_revision` 不匹配时返回 `REVISION_CONFLICT`。
- `actor` 不来自请求体，而是从认证令牌派生：

```python
class AuthenticatedActor(StrictModel):
    actor_type: Literal["seat", "host"]
    seat_id: int | None
    room_id: UUID
```

- 座位命令要求 `actor_type == "seat"`，且当前令牌的 `seat_id` 与状态机解析出的行动座位一致。
- 主持人命令要求 `actor_type == "host"`。
- 所有 Pydantic 契约继承 `StrictModel`，禁止额外字段和隐式类型转换。
- `PlayerCommand` 与 `HostCommand` 是 `command_type` 判别联合；类型集合与 RulePack 的可执行命令表一一对应。
- `PlayerCommand` 至少包含：
  `JOIN_ROOM`、`SET_READY`、`CONFIRM_ROLE`、`WOLF_NOMINATE_KILL`、`SEER_INSPECT`、`WITCH_USE_ANTIDOTE`、`WITCH_USE_POISON`、`WITCH_SKIP`、`SPEAK`、`PASS_SPEECH`、`VOTE`、`ABSTAIN`、`RECONNECT`。
- `HostCommand` 至少包含：
  `HOST_PAUSE`、`HOST_RESUME`、`HOST_PATCH`、`HOST_REWIND_TO_SNAPSHOT`、`HOST_FORCE_TEMPLATE`。
- `HOST_PATCH` 使用独立判别联合：

```python
class SetAlivePatch(StrictModel):
    patch_type: Literal["SET_ALIVE"]
    seat_id: int
    alive: bool

class SetRolePatch(StrictModel):
    patch_type: Literal["SET_ROLE"]
    seat_id: int
    role: Role

class SetPotionPatch(StrictModel):
    patch_type: Literal["SET_POTION"]
    antidote_available: bool
    poison_available: bool

class SetVotePatch(StrictModel):
    patch_type: Literal["SET_VOTE"]
    voter_seat_id: int
    round_id: UUID
    target_seat_id: int | None

class SetSeerChecksPatch(StrictModel):
    patch_type: Literal["SET_SEER_CHECKS"]
    seer_seat_id: int
    checks: list[SeerCheckRecord]

class SetPhasePatch(StrictModel):
    patch_type: Literal["SET_PHASE"]
    phase: Phase

HostPatch = Annotated[
    SetAlivePatch
    | SetRolePatch
    | SetPotionPatch
    | SetVotePatch
    | SetSeerChecksPatch
    | SetPhasePatch,
    Field(discriminator="patch_type"),
]
```

`SetPhasePatch` 只允许回到当前阶段的开始状态；需要跨阶段恢复时必须使用 `HOST_REWIND_TO_SNAPSHOT`。

### 5.2 命令结果

```python
class CommandResult(StrictModel):
    schema_version: Literal["command-result.v1"] = "command-result.v1"
    command_id: UUID
    accepted: bool
    revision: int
    event_ids: list[UUID]
    error_code: CommandErrorCode | None
```

命令结果只说明是否接受。视图通过独立投影读取。

`accepted == false`、revision 冲突、重复 `command_id` 和未改变状态的幂等命令都不增加 revision。只有成功应用并改变状态或产生新领域事件的命令才增加 revision。

### 5.3 事件可见性

```python
class PublicVisibility(StrictModel):
    scope: Literal["public"] = "public"

class SeatVisibility(StrictModel):
    scope: Literal["seat"] = "seat"
    seat_id: int

class FactionVisibility(StrictModel):
    scope: Literal["faction"] = "faction"
    faction: Faction

class HostVisibility(StrictModel):
    scope: Literal["host"] = "host"

EventVisibility = Annotated[
    PublicVisibility | SeatVisibility | FactionVisibility | HostVisibility,
    Field(discriminator="scope"),
]
```

### 5.4 领域事件

```python
class DomainEvent(StrictModel):
    schema_version: Literal["event.v1"] = "event.v1"
    event_id: UUID
    room_id: UUID
    revision: int
    event_type: EventType
    visibility: EventVisibility
    fact_payload: dict[str, JsonValue]
    causation_id: UUID | None
    correlation_id: UUID
    created_at: datetime
```

`fact_payload` 是通过 `EventType` 对应的 Pydantic 模型校验后导出的结构化事实，不允许任意玩家文本冒充事实。

### 5.5 视图

```python
class PublicView(StrictModel):
    schema_version: Literal["public-view.v1"]
    room_id: UUID
    revision: int
    phase: Phase
    day: int
    living_seats: list[int]
    public_timeline: list[PublicTimelineItem]
    vote_summary: VoteSummary | None
    deadline_at: datetime | None

class SeatView(PublicView):
    seat_id: int
    role: Role | None
    private_facts: list[PrivateFact]
    legal_actions: list[LegalAction]
```

`SeatView` 是服务端生成对象，不是客户端根据 `PublicView` 自行扩展。

## 6. 命令处理流程

```text
1. 鉴权
2. 解析 CommandEnvelope
3. command_id 去重检查
4. expected_revision 检查
5. 判断 PAUSED / HOST_RECOVERY
6. 状态机验证命令
7. 纯函数应用命令
8. 生成事件并提升 revision
9. 保存命令、事件和必要快照
10. 计算新投影
11. 触发下一阶段
12. 按授权范围 WebSocket 推送
```

1-11 是本地事务。任何异常都不能留下“状态已变但事件未写”的半成品。

房间内命令按到达顺序串行处理。两个客户端基于同一 revision 并发提交时：

1. 第一份命令成功，revision 增加。
2. 第二份命令返回 `REVISION_CONFLICT`，不改变状态。
3. 冲突结果不写入 `command_id` 去重缓存。
4. 客户端刷新后，仅在原操作仍合法且用户意图未变时可自动重试一次，并使用新的 `command_id`。
5. 测试验收最终票数，不要求两个旧 revision 命令直接同时成功。

## 7. AI DM 管线

### 7.1 两类播报

| 类型 | 生成方式 | 是否调用 LLM |
| --- | --- | --- |
| 高频流程 | 固定模板 | 否 |
| 复杂叙事 | 事实模板 + LLM 润色 | 是 |
| 规则解释 | 固定规则检索 + 模板 | 默认否 |
| 错误恢复 | 错误模板 | 否 |

### 7.2 输入上下文

LLM 只能收到 `DMRenderContext`：

```python
class DMRenderContext(StrictModel):
    context_id: UUID
    intent: Literal[
        "ANNOUNCE",
        "PROMPT_ACTION",
        "SUMMARIZE_PUBLIC",
        "EXPLAIN_RULE",
        "RECOVER",
    ]
    channel: Literal["public", "seat", "host"]
    audience_seat_ids: list[int]
    phase: Phase
    allowed_fact_ids: list[UUID]
    allowed_facts: list[DmFact]
    forbidden_patterns: list[str]
    template_text: str
    template_variant_id: str
    style: Literal["neutral", "tense", "ceremonial"]
```

`allowed_facts` 由服务端从已授权事件投影生成。

`forbidden_patterns` 只作为生成阶段提示，帮助模型避开已知错误；它不是安全边界，不能替代 Pydantic、事实白名单和正则过滤。

### 7.2.1 Context Builder

`ContextBuilder` 是把 `GameState` 和事件日志转换为有限、可审计上下文的核心模块：

```python
class ContextBuilder:
    def build_dm_context(
        self,
        state: GameState,
        events: list[DomainEvent],
        intent: DMRenderIntent,
        channel: Channel,
        audience_seat_ids: list[int],
        template_variant_seed: int,
    ) -> DMRenderContext:
        ...
```

约束：

- 是纯函数。相同 state、events、intent、channel、audience 和 seed 必须产生相同 context。
- 不读取当前时间、全局随机数、环境变量或数据库连接。
- 先按 `visibility` 过滤事件，再生成 `allowed_fact_ids` 和 `allowed_facts`。
- `channel == "public"` 时不能包含任何座位私密事实。
- `channel == "seat"` 时只能包含该座位已依法拥有的事实。
- 模板变体只能从当前事实类型对应的变体池中选择，并把 `template_variant_id` 写入 context。
- Context Builder 的输出进入 `dm_trace` 作为审计证据。

### 7.3 结构化输出

```python
class DMRenderOutput(StrictModel):
    schema_version: Literal["dm-render.v1"] = "dm-render.v1"
    prefix: str = Field(max_length=80)
    suffix: str = Field(max_length=80)
    fact_refs: list[UUID] = Field(min_length=1)
    style: Literal["neutral", "tense", "ceremonial"]
```

最终播报为：

```text
prefix + template_text + suffix
```

事实句 `template_text` 始终由服务端硬编码，LLM 不能改写中间事实句。`prefix` 和 `suffix` 不得包含座位号、数字、角色名或行动结果。`DMRenderOutput` 不包含角色、死亡、票数、胜负或阶段字段，因此 LLM 没有状态修改入口。

### 7.4 输出门

LLM 输出必须依次通过：

1. NFKC 规范化。
2. 移除零宽字符、双向控制字符和不可见格式字符。
3. 全角/半角和政治数字统一。
4. Pydantic 解析。
5. `fact_refs` 是否全部属于 `allowed_fact_ids`。
6. `prefix`、`suffix` 不得包含座位号、数字、角色名或行动结果。
7. 当前频道敏感信息正则。
8. 隐藏角色直接指认模式检查。
9. 夜间行动、药水、查验和狼队信息泄漏检查。

任何一步失败：

```text
discard LLM output
-> render template_text
-> append dm_fallback event
-> continue state machine
```

### 7.5 语气、介入与模板变体

语气和介入时机必须由硬编码状态规则决定：

| 条件 | 行为 |
| --- | --- |
| 玩家正常发言 | 不插话 |
| 发言剩余 10 秒 | 发送时间提醒 |
| 投票剩余 5 秒 | 发送时间提醒 |
| 进入 PK | 发送 PK 规则说明 |
| 同一玩家连续两轮未行动 | 定向提示该玩家 |
| 首夜或终局 | 使用 `ceremonial` |
| 存活人数不超过 3 且进入第 3 天 | 使用 `tense` |
| 其他状态 | 使用 `neutral` |

高频模板维护 3-5 个语义等价变体。变体通过 `template_variant_seed` 选择，并把 `template_variant_id` 写入事件和 trace，保证回放一致。变体不能增加、删除或修改事实。

建议的介入规则和变体选择不得调用 LLM 做判断。

### 7.6 润色缓存

润色缓存默认关闭。启用时必须使用完整上下文键：

```text
prompt_version
+ model_id
+ intent
+ channel
+ audience_scope_hash
+ ordered_fact_ids
+ fact_content_hash
+ template_variant_id
+ style
```

- 不允许仅按 `intent + channel + style + phase` 缓存。
- 公共频道缓存与座位频道缓存必须物理或逻辑隔离。
- 私密频道默认不缓存。
- 高频模板本身不调用 LLM，不使用润色缓存。

### 7.7 延迟预算

| 阶段 | 目标 | 硬限制 |
| --- | ---: | ---: |
| 事实模板与最终回退 | 20 ms | 100 ms |
| LLM 调用 | 1.0 s | 1.5 s 取消 |
| 输出门 | 50 ms | 100 ms |
| 有序 outbox 与广播 | 200 ms | 300 ms |
| 总计 | 1.27 s | 2.0 s |

绝对截止时间为 `trigger_at + 2.0 s`。`trigger_at + 1.5 s` 时仍未形成合规完整输出，则终止 LLM 任务；剩余 500 ms 用于保留模板、输出门和广播的最坏路径。延迟测量覆盖从阶段触发到本地局域网客户端进入已排序消息队列的完整路径，并用最大值/p99 证明硬上限，不用 p95 代替。

## 8. 敏感信息过滤

### 8.1 动态公开真值集

公共频道只允许：

- 已公开的死亡和存活状态。
- 已公开票型。
- 规则允许公开的阶段和时间信息。
- 玩家公开发言中的自称内容，仅以“某玩家声称”呈现。

公共频道禁止：

- 未公开身份。
- 狼队成员和击杀选择。
- 预言家查验记录。
- 女巫药水状态和使用记录。
- 任何由 LLM 推断出的隐藏事实。

### 8.2 过滤器组成

- 固定敏感词表：`狼人队友`、`查验结果`、`解药`、`毒药` 等上下文敏感词。
- 动态角色指认正则：座位号 + 身份断言。
- 动态事实模式：未公开事件类型 + 座位号。
- 座位白名单：允许提及的公共座位。
- 数字白名单：允许出现的票数和计时。

过滤前统一执行 NFKC、去零宽字符、去双向控制字符、全半角统一、中文数字归一和角色别名归一。词表过滤器只能作为最后一道防线，不能替代事实白名单和 `prefix + template + suffix` 结构。

## 9. 暂停、恢复和纠错

### 9.1 暂停

进入暂停时：

- 记录每个计时器的 `remaining_ms` 并取消计时器回调。
- 标记当前 LLM 任务不可发布，并递增长任务代次使迟到响应自动失效。
- 拒绝除主持人和重连外的玩家命令。
- 保存暂停快照。

### 9.2 恢复

恢复前执行：

1. 规则状态不变量。
2. 票数和行动集合一致性。
3. 可见性投影一致性。
4. 未完成任务的失效检查。

全部通过后：

1. 原阶段计时器按 `remaining_ms` 继续。
2. 暂停前未完成的 LLM 任务直接丢弃，不发布迟到结果。
3. 如果当前状态需要播报，重新生成一次；在 1.5 秒内未完成则强制模板。
4. 不需要玩家行动但仍需播报的状态，模板完成后立即推进。

### 9.3 纠错

- 优先使用 `HOST_REWIND_TO_SNAPSHOT`。
- 小范围修复使用类型受限的 `HOST_PATCH`。
- 纠错先在状态副本上应用并校验；失败时原状态不变，只记录拒绝事件。
- 成功时在同一事务提交状态、前后差异、补偿事件、重建投影和新 revision。
- 状态、`HOST_CORRECTION_APPLIED`、差异、补偿事件或重建投影任一步持久化失败时，整个事务回滚，revision 不增加。
- `HOST_PATCH` 不修改已经落账的历史事件；历史修正必须回退快照并重新产生事件。
- 原始事件永久保留。
- 纠错后的 revision 必须单调递增。

## 10. 实时协议

### 客户端到服务端

- REST：创建房间、加入房间、健康检查、导出回放。
- WebSocket：游戏命令、心跳、重连、视图订阅。

### 服务端到客户端

- `view.updated`
- `room.paused`
- `room.resumed`
- `dm.message`
- `game.ended`
- `error`

公共频道只收到公共消息。每个座位频道只收到其授权视图。

### 有序 outbox

- 每个房间维护单调递增 `outbox_seq`。
- `dm.message`、`game.ended` 和视图更新进入同一有序 outbox。
- 死亡或放逐公告先获得较小的 `outbox_seq`，终局消息不得越过它提前发布。
- 逻辑状态可以立即进入 `WIN_CHECK`，但客户端消息顺序由 outbox 保证。

## 11. 前端设计约束

- 移动端优先。
- 玩家界面只显示当前合法操作。
- 夜间行动必须在独立遮罩中完成，避免旁观者看到。
- 共享屏不渲染任何私密角色、药水或查验信息。
- 暂停状态必须清晰显示，玩家不能继续提交行动。
- 主持人控制台与玩家界面物理分离或在桌面模式授权进入。

## 12. 可观测性

每局至少记录：

- command id、revision、状态转移。
- 计时器开始、取消和超时。
- LLM provider、模型、提示词版本、耗时、token、成本。
- LLM 原始结构化输出、过滤器结果和最终输出。
- 降级原因。
- 主持人纠错 diff。

日志中不得记录完整座位秘密，除非是本地开发诊断且显式开启。

### 导出权限

- 主持人完整审计导出：允许包含全量事件、快照、`dm_trace` 和角色真值。
- 玩家回放导出：只允许公共事件和该玩家本人已经拥有的私密事实。
- 原始 `dm_trace`、快照和全量事件不能被玩家令牌读取。
- 座位频道不能跨座位订阅。

## 13. 安全边界

- 玩家文本是不可信输入。
- 玩家不能通过发言创建规则事件。
- LLM 不能读取未经授权的事实。
- 主持人令牌与玩家座位令牌分离。
- 调试接口默认关闭。
- API Key 只通过环境变量或本地 Secret 注入。

## 14. Changelog

### v1.1.0 - 2026-09-23

- 显式声明 Event Sourcing 当前边界和未来升级条件。
- 增加纯函数 `ContextBuilder` 和 `forbidden_patterns` 生成提示。
- 增加硬编码语气、沉默和介入策略。
- 增加确定性模板变体池。
- 增加安全缓存键约束；拒绝仅按意图、频道、语气和阶段复用。
- 保留 Prompt 预防约束，同时保留全部事后事实与泄漏过滤。
