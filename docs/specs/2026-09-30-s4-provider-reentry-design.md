---
spec_id: s4-provider-reentry-design
version: 0.1.0
status: proposed
proposed_at: 2026-09-30
frozen_at: null
owner: architecture
depends_on:
  - s4-ai-dm-constitution@1.1.0
  - s4-ai-dm-design@1.1.6
  - verification-matrix@1.5.6
  - product-constitution@1.1.0
  - system-design@1.1.0
supersedes: null
---

# S4 Provider 重入设计 v0.1.0

## 1. 状态与效力

本文件是 `S4-RE-*` 的提案设计，状态为 `proposed`，不是 frozen 规格，
也不授权生产实现。当前 `S4-01` 至 `S4-07` 的 template-only 冻结语义保持
不变；只有在本文件完成独立复核、版本冻结并获得用户明确授权后，才能进入
真实 provider 适配器、运行时异步 admission 和默认关闭开关的生产集成。

本设计明确区分三类事实：

1. 服务端规则、状态、事实、模板文本和发布顺序是权威事实；
2. provider 只返回服务端短语目录中的 ID，不拥有事实，也不能生成自由文本；
3. 最终发布文本是服务端对权威模板文本和受控短语的组合，provider 输出始终
   是不可信输入，任何失败都不能改变游戏状态。

## 2. 目标与非目标

### 2.1 目标

- 在模板路径始终可用的前提下，为低频公共公告增加可丢弃的 provider 润色。
- 只向 provider 发送 public-only 的最小上下文，不发送 seat、host、token、
  session、隐藏角色、狼队、药水、查验或未公开死因。
- 让 provider 调用异步于 `RoomActor`，不阻塞命令、暂停、恢复、重连或
  状态推进。
- 保持原冻结硬门禁：provider 取消 `1.5s`、模板回退 `1.6s`、绝对
  admission `2.0s`；继续使用 `p99 < 1.2s`、`maximum < 1.5s`、
  provider failure `< 5%`、spike acceptance `>= 50%`。
- 所有 provider 失败、超时、拒答、截断、schema 错误、输出门失败、迟到、
  跨 revision/phase/room/session/epoch 结果都丢弃并回退模板。
- 默认关闭；未通过 spike、未冻结预算、未获得用户批准时不得启用真实
  provider。

### 2.2 非目标

- 不做流式输出、多轮 provider 对话、跨局记忆、向量数据库、玩家推理或
  模型裁决。
- 不让 provider 接触 seat context、host context、原始事件、完整
  `GameState`、数据库、房间令牌或任何私有 facts。
- 不做多 provider 智能路由、自动重试、玩家可见重试、动态 schema
  生成或模型驱动的规则解释。
- 不在本设计中修改 S1/S2/S3 的状态机、协议、权限和回放边界。

## 3. 与现有规格的关系

当前冻结的 `s4-ai-dm-design@1.1.6` 和
`s4-ai-dm-constitution@1.1.0` 将 provider 全部标记为 deferred。
本设计不直接改写这些文件，而是定义独立的重入路径：

- schema 和测试使用新的 `S4-RE-*` ID；
- 现有 `S4-*` template-only 行保持原语义；
- 新设计冻结时，验证矩阵应提升到 `v1.6.0`，S4 design 应提升到
  `v1.2.0`（或用户确认的等价版本），并保留旧 ID 的历史 owner 与证据。
- 只有新设计冻结后，`docs/specs/2026-09-26-s4-ai-dm-design.md` 的 provider
  附录才能从“未来重入”升级为 active 条款。

## 4. 方案比较

### 4.1 方案 A：在 `RoomActor` 内同步等待 provider

优点是实现最少；缺点是网络延迟会阻塞 actor 队列，可能让暂停、恢复、
重连、状态推进和终局处理全部等待网络。该方案与 provider 可失败、模板必须
立即可用的冻结原则冲突，拒绝。

### 4.2 方案 B：provider 直接生成最终自由文本

优点是叙述自由度最高；缺点是模型可以改写事实、拼接玩家声称、引入不存在的
数字/座位/角色/结果，且输出门无法只靠 schema 保证语义安全。该方案违反
事实所有权和 claim isolation 原则，拒绝。

### 4.3 方案 C：模板先行、provider 选择受控前后缀短语

服务端先确定性生成 `base_text`，provider 只返回 `prefix_phrase_id` 和
`suffix_phrase_id`，再由服务端从冻结短语目录组装最终文本：

```text
final_text = prefix_text + base_text + suffix_text
```

provider 不提供自由文本，不能改变事实、数字、座位、角色、行动或结果；
异步 provider 任务在 `RoomActor` 外部运行，结果只通过受 generation guard
保护的事件回到 actor。该方案满足最小改动、失败关闭和可验证性要求，作为
选定架构。

## 5. 选定架构

### 5.1 组件

| 组件 | 责任 | 禁止事项 |
| --- | --- | --- |
| `TemplateDMService` | 继续生成服务端权威 `base_text` | 不调用 provider，不阻塞 |
| `DMProviderPort` | provider-neutral 异步端口 | 不接触 `GameState` 或 room token |
| `OpenAICompatibleDMProvider` | 用已有 `httpx` 发送单次非流式请求 | 不添加 SDK，不自动重试 |
| `PhraseCatalog` | 冻结的公共前后缀短语目录 | 不包含座位、数字、角色或结果 |
| `DMProviderPolicy` | 判断 eligibility、构建请求、组装和校验输出 | 不修改模板文本和游戏状态 |
| `DMProviderRuntime` | 管理每房间最多一个异步调用、取消和 outcome 入队 | 不保存权威状态 |
| `RoomActor` | 在自身串行队列中接纳最终结果或模板回退 | 不在网络等待点持有状态锁 |
| `DMProviderTrace` | 记录裁剪后的状态、延迟和错误类别 | 不记录原始 prompt、原始输出、密钥或 token |

### 5.2 数据流

```text
已提交的领域事件
  -> TemplateIntentSelector / TemplateRenderer
  -> base_text 与 base_text_sha256（服务端权威）
  -> DMProviderPolicy 判断是否 eligible
      -> 不可用：立即走既有模板 admission
      -> 可用：RoomActor 登记 pending slot
               并启动异步 provider 任务
  -> provider 返回受控 phrase ID 建议
  -> output gate 校验 digest / allowlist / Unicode / 事实不变性
  -> RoomActor 在 1.6s fallback 前接纳组合文本
  -> 失败或迟到：丢弃 suggestion，接纳 base_text
  -> 2.0s 绝对 deadline 前必须 admitted 或 suppressed
```

### 5.3 时序预算

| 时点 | 行为 |
| --- | --- |
| `slot.trigger_at = T` | 服务端先完成确定性 `base_text` 渲染，并登记 pending slot |
| `T + 1500ms` | provider 请求 hard cancel；之后到达的结果无效 |
| `T + 1600ms` | actor 必须优先使用模板回退，不再等待 provider |
| `T + 2000ms` | 绝对 admission 上限；仍未接纳则 suppressed 并写入终态 trace |

provider 请求的实际超时取 `min(1500ms, T + 1500ms - now)`；如果 actor
开始任务时已越过 provider cancel 点，则不发起网络请求，直接模板回退。

## 6. 核心契约

### 6.1 `ProviderSuggestion`

```python
class ProviderSuggestion(StrictModel):
    schema_version: Literal["s4-re.v1"]
    base_text_sha256: str  # ^[0-9a-f]{64}$
    prefix_phrase_id: str | None
    suffix_phrase_id: str | None
    style: Literal["neutral", "warm"]
```

至少一个 phrase ID 必须非空。ID 只允许 `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`。
原始 provider 文本不进入任何持久化或玩家可见路径。

### 6.2 `ProviderRequest`

```python
class ProviderRequest(StrictModel):
    job_id: UUID
    base_text: str
    base_text_sha256: str
    category: Literal["PHASE_NOTICE"]
    style: Literal["neutral", "warm"]
    allowed_prefix_phrase_ids: tuple[str, ...]
    allowed_suffix_phrase_ids: tuple[str, ...]
    deadline_monotonic_ms: int
```

`job_id` 只在进程内使用；请求不包含 `room_id`、`seat_id`、`session_id`、
玩家名、token 或事件原文。

### 6.3 `PhraseCatalog`

每个短语条目只包含：

```python
class PhraseEntry(StrictModel):
    phrase_id: str
    text: str
    category: Literal["PHASE_NOTICE"]
    position: Literal["prefix", "suffix"]
    style: Literal["neutral", "warm"]
```

目录规则：

- 文本只能是无数字、无座位号、无角色名、无行动结果的安全风格词；
- 同一 `(category, position, style)` 的短语必须唯一且完整枚举；
- 目录版本化且不可变；
- provider 只能选择请求中给出的 allowlist ID；
- 最终文本必须满足 `prefix_text + base_text + suffix_text`，且
  `base_text` 在原文本中恰好出现一次。

### 6.4 provider outcome

```python
ProviderOutcomeStatus = Literal[
    "accepted",
    "timeout",
    "cancelled",
    "transport_error",
    "http_error",
    "schema_error",
    "refusal",
    "truncated",
    "unsafe",
]

class ProviderOutcome(StrictModel):
    status: ProviderOutcomeStatus
    suggestion: ProviderSuggestion | None
    provider_id: str
    model_id: str
    latency_ms: int
```

`accepted` 只表示 HTTP 和 schema 层通过，不表示已经可以发布；最终发布还要
通过 `DMProviderPolicy` 输出门和 `RoomActor` 状态复检。

### 6.5 最终消息来源

模板路径保持 `source="template"`。provider 参与组合后的消息使用
`source="template+provider"`。前端协议只增加这一个字面量，不增加自由
文本、prompt、provider 状态或模型字段。

## 7. Provider 候选与 spike 协议

### 7.1 候选池

`S4-RE-01` 必须使用同一批 prompt、schema、短语目录和输出门比较 2 到 3 个
候选。建议候选族：

1. Groq OpenAI-compatible 低尾延迟模型，例如当前可用的 `gpt-oss` 小模型；
2. Cerebras OpenAI-compatible 低尾延迟模型，例如 `gpt-oss-120b`；
3. 本机 Ollama OpenAI-compatible 路径，但当前 `ollama list` 为空；
   在用户明确批准拉取模型前，不得把本地模型写成可用候选。

现有 DeepSeek 结果只作为失败证据，不得作为 default；重新测试也必须是新
样本、新 prompt version 和新报告。

### 7.2 样本与环境

- 复用 `S4_SPIKE_CANDIDATES_JSON` 提供候选列表；每个对象包含
  `provider`、`model`、`base_url_env`、`api_key_env`、`network`，可选
  `response_format`。环境变量只提供名称，不在报告、日志或对话中展开
  密钥值。
- 每个候选 20 次不计分 warmup、100 次 cold、100 次 warm；
- 每次调用使用同一 system prompt、同一 strict `json_schema`、同一
  `temperature=0`、同一 `max_tokens` 和同一输出门；
- 记录 provider、model、endpoint 类型、region、transport、tier、
  硬件、网络、prompt version、schema SHA-256、冷/热阶段；
- 失败样本保留原始错误类别和原始计数，不得只给成功样本；
- 不允许把未配置、未拉模型或网络不可用伪装成通过。

### 7.3 硬门禁

| 指标 | 阈值 |
| --- | --- |
| p99 | `< 1200ms` |
| maximum | `< 1500ms` |
| provider failure rate | `< 5%` |
| spike acceptance rate | `>= 50%` |
| default provider | 仅在至少一个候选全部通过时选择 |

如果所有候选失败，`selected_provider=none`，停止真实 provider 适配和
生产启用；可以保留 fake provider、契约和输出门的独立测试，但不得把
它们写成真实重入完成。

## 8. 异步 admission 与 generation guard

### 8.1 每房间规则

- 每房间最多一个 in-flight provider job；
- provider job 只在 `RoomActor` 的事件循环外运行；
- actor 不等待网络结果，只处理已入队的 outcome 和 deadline；
- 后续 slot 在 head pending 未完成时不得越过前者进行 DM admission；
- provider 结果迟到、失败、busy、stale 或输出门拒绝时，actor 在模板
  fallback 可用时立即接纳模板；
- 暂停、重连、revision/phase 变化、恢复 epoch 变化、房间关闭或
  终局快照变化时，旧 generation 结果必须失效；
- provider job 不持久化；进程重启后从模板路径恢复，不重新发起旧 job。

### 8.2 generation 与失效条件

每个 provider job 绑定：

```text
(room_id, recovery_epoch, domain_seq, intent_id, source_event_id,
 source_revision, source_phase, base_text_sha256, generation)
```

任一字段不一致即丢弃结果。`RoomActor` 绝不能因为 provider 结果而修改
`GameState`、outbox item、revision 或规则事实。

### 8.3 终局顺序

当更早的 DM slot 仍处于 pending 时，`GAME_END` 的公共 view 更新必须
延迟到该 slot admitted 或 suppressed；非终局 view 更新可以继续发送，但
不能把 provider 结果写入状态或改变 wire sequence 的既有单调性。

## 9. 信息边界与安全

- provider 只接收 public `PHASE_NOTICE` 的最小 `base_text`、公开事实
  摘要和 phrase allowlist；
- 不发送 `GameState`、原始事件、seat/host context、token、session、
  玩家声称、隐藏角色、药水、查验、死因或数据库对象；
- provider 输出视为不可信用户输入；任何 refusal、截断、额外字段、
  Markdown、控制字符、Unicode 同形字或 prompt 泄漏都 fail closed；
- `Authorization` 头、API key、base URL 中的凭据和原始 prompt/output
  不进入日志、trace、指标或审计 API；
- 输出门同时校验 schema、digest、phrase allowlist、Unicode、长度、
  事实不变性和精确拼接关系；
- 结构合法不等于语义安全；没有通过输出门的 `accepted` 结果仍然回退模板。

## 10. 指标、Trace 与隐私

当 provider 关闭时，现有 `/metrics` 和 trace 输出保持不变。provider
启用时增加一个独立可选 payload，不把 provider 字段塞进既有
`DMTraceRecord`：

```text
eligible
provider_attempted
provider_admitted
provider_fallback
provider_suppressed
provider_skipped_busy
provider_latency_ms_p95
provider_latency_ms_p99
provider_latency_ms_max
provider_hit_rate
provider_fallback_rate
```

终态守恒：

```text
eligible == provider_admitted + provider_fallback + provider_suppressed
```

`provider_attempted` 是信息性计数，可因 `skipped_busy` 与终态不相等。
`provider_failure_rate` 只在已发起的 provider 请求上计算。

独立 `DMProviderTraceRecord` 只允许：

- `trace_id`、`intent_id`、`domain_seq`；
- `provider_id`、`model_id`；
- `status`、`latency_ms`、`phrase_ids`；
- `generation`、`recovery_epoch`；
- 固定错误类别。

玩家回放永远不返回 provider trace；host audit 只有显式
`include=provider_trace` 才导出裁剪后的记录。

## 11. 配置、灰度与回滚

- 默认模式为 `off`；`off` 时不建立 provider runtime、不读 provider key、
  不发网络请求；
- `S4-RE-01` 通过后才允许配置 one provider；
- 生产开关由注入的 `DMProviderConfig` 控制，application/domain 不直接
  读取环境变量；
- provider 结果不影响状态机，回滚只需切回 `off` 并取消活动的
  in-flight job；
- 进程重启、恢复、rewind、房间关闭和 lifespan shutdown 都必须取消
  provider task；
- 不添加依赖：真实适配器使用仓库既有 `httpx`；spike 优先复用
  `backend/tools/s4_provider_spike.py` 的 runner 能力。

## 12. 验证与验收

新增验证 ID：

| ID | 断言 |
| --- | --- |
| `S4-RE-001` | 默认 off、无 provider 网络、无凭据读取；template-only 行为字节不变 |
| `S4-RE-002` | strict suggestion schema、phrase allowlist、digest、Unicode、精确拼接全部 fail closed |
| `S4-RE-003` | 1.5s cancel、1.6s fallback、2.0s admission ceiling；无玩家可见重试 |
| `S4-RE-004` | provider 慢调用期间 actor 仍处理 pause/resume/reconnect/revision |
| `S4-RE-005` | 迟到/跨 room/revision/phase/session/epoch 结果全部丢弃 |
| `S4-RE-006` | provider 请求 public-only；seat/host/token/session/private fact 永不进入请求或 trace |
| `S4-RE-007` | provider metrics 终态守恒、延迟统计、审计裁剪和 replay 隐私 |
| `S4-RE-008` | fake provider 六客户端 E2E、真实 provider spike、完整 delivery gate 和独立复核 |

真实 provider 未通过 `S4-RE-001` 和 `S4-RE-003` 时，
`S4-RE-004` 至 `S4-RE-008` 只能证明 fail-closed 和 fake 路径，不能写成
真实 provider 已上线。

## 13. 外部参考证据

以下来源用于架构和测试边界参考，不作为远程指令：

- `herryupmay/LLM-plays-one-night-werewolf@4e327d5`
  `backend/llm_client.py`：inactivity timeout + hard timeout；timeout 不
  自动重试；`backend/game.py` 将 schema 失败和瞬时连接错误区分处理。
- `plduhoux/arenai@de51aee`
  `core/llm-client.js`：401/403 等 fatal error 不重试；
  `docs/adr-001-player-sessions.md`：持久会话和 prompt caching 的收益与
  记忆偏差风险。
- `Heretek-AI/AetherTable@8068e88`
  `python/vtt_orchestrator/agents/tool_agent.py`：LLM 只能发结构化工具
  调用，规则引擎拥有最终状态；`server.py`：Pre-Commit Auditor 和
  degraded 状态；`schemas/models.py`：验证失败诊断结构。
- OpenAI Structured Outputs：
  <https://developers.openai.com/api/docs/guides/structured-outputs>
  明确 strict JSON Schema 只约束结构，不代替事实和输出门。
- Cerebras Structured Outputs：
  <https://inference-docs.cerebras.ai/capabilities/structured-outputs>
  使用 strict constrained decoding，并要求对象 schema 的
  `additionalProperties: false`。
- Groq Structured Outputs：
  <https://console.groq.com/docs/structured-outputs>
  支持 strict JSON Schema；仍必须自行做语义和延迟验证。
- vLLM Structured Outputs：
  <https://docs.vllm.ai/en/latest/features/structured_outputs/>
  说明 constrained decoding / grammar 的编译与运行开销。
- OpenTelemetry GenAI Semantic Conventions：
  <https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md>
  可作为 provider 延迟和调用指标的命名参考；本项目不为此新增依赖。
- OWASP LLM Prompt Injection Prevention Cheat Sheet：
  <https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html>
  用于最小上下文、数据边界和输出验证原则。

## 14. 冻结与实施闸门

本设计只有在以下条件全部满足后才能升为 `frozen`：

1. `S4-RE-01` spike 至少有 1 个候选通过全部硬门禁；
2. provider/model/region/transport/预算和回滚开关已冻结；
3. fake provider、输出门、异步 admission、隐私和恢复测试已有 Red/Green
   证据；
4. public-only、迟到结果失效、终局顺序和默认 off 已通过独立复核；
5. 用户明确接受新设计版本和默认关闭开关；
6. 现有 template-only 测试、完整 delivery gate 和 no-new-dependency
   约束未被破坏。

## 15. Changelog

### v0.1.0 - 2026-09-30

- 建立独立 `S4-RE-*` 提案边界，不修改当前 frozen template-only 语义。
- 选择“模板先行 + 受控短语 ID + 异步 admission”方案。
- 固化 public-only、1.5s/1.6s/2.0s、默认 off、generation guard、
  fail-closed 输出门、provider metrics 和 spike 协议。
