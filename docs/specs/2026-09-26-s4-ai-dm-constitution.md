---
spec_id: s4-ai-dm-constitution
version: 1.1.0
status: frozen
proposed_at: 2026-09-27
frozen_at: 2026-09-27
owner: product
depends_on:
  - product-constitution@1.1.0
  - system-design@1.1.0
  - s3-frontend-constitution@1.1.0
supersedes: s4-ai-dm-constitution@1.0.0
---

# S4 AI DM 产品宪法 v1.1

## 1. 状态与效力

本文件是 S4 AI DM 的产品宪法，状态为 `frozen`。它固化用户已确认的
`S4-D1` 至 `S4-D8`，并根据 `S4-P0-01a` / `S4-P0-01b` 结果把当前
S4 的活动范围收窄为 `template-only`。

v1.0.0 面向 provider-neutral 与可选 LLM 润色。v1.1.0 将那些条款标记为
`deferred`，当前实现和验收只接受模板路径。provider 相关条款不能继续
定义当前 schema、指标、延迟或完成条件。

本文件已冻结，但不授权实现。实现任务必须先有单独用户授权。

## 2. 产品定义

S4 的 AI DM 是确定性主持层，不是第二主持人，也不是开放式叙事代理。

当前 S4 只能：

- 从服务端已提交状态和公开事件中选择模板意图；
- 从冻结模板目录选择确定性变体；
- 只使用授权事实渲染文本；
- 通过既有 RoomActor 顺序发布模板消息。

当前 S4 不能：

- 调用远端或本地 LLM；
- 创建 provider job；
- 读取 API key、endpoint、model 或网络配置；
- 修改游戏状态；
- 创造规则事实；
- 把玩家声称当作裁判事实；
- 读取未授权秘密；
- 在玩家正常发言时主动插话。

## 3. 已确认的八项原则

### S4-D1 首发范围

AI DM 只做受约束叙述、确定性流程引导和已冻结规则/状态模板解释。

不做自由 DM 对话、玩家侧 AI 军师、AI 补位玩家、主动追问或隐藏身份推理。

### S4-D2 模板与 LLM 分流（template-only）

- 当前 S4 只交付确定性模板 DM；模板是唯一默认来源和唯一发布路径。
- 高频、状态关键、暂停恢复、行动提示、提醒和错误全部模板化。
- 当前 S4 不产生 `llm_eligible`，不创建 provider job，不调用或等待 LLM，
  也不把模型输出、命中率或延迟纳入本轮验收。
- 事实句、模板变体、观众绑定和发布顺序始终由服务端决定。
- LLM 前后缀润色为 deferred 能力；重新进入必须另立版本化任务，完成新的
  provider 选型、预算冻结、验证矩阵更新和用户授权。

### S4-D3 Provider 与调用边界（deferred）

- 当前 S4 冻结 `provider=none`、`llm_enabled=false`；不绑定 endpoint、
  model、region 或 transport，不创建 provider adapter、API key 或
  provider job。
- 当前 S4 不接受 provider 延迟、失败率、命中率、cold/warm 或模型质量
  证据，也不把网络可用性作为 DM 发布前置条件。
- provider-neutral、OpenAI-compatible、单次非流式、1.5 秒取消、1.6 秒
  fallback、2.0 秒 admission、每房间最多一个 in-flight public job，仅作为
  未来重新进入的候选契约。
- 未来 provider 只允许 public context；seat 只用于模板，host 不进入 LLM。
- 重新启用必须完成真实路径 `LAT-002/003`、隐私、停用/降级和独立复核。

### S4-D4 降级（template-only）

- 当前 S4 的模板路径是基线路径，不是失败后的备用路径。
- 当前 S4 不调用 provider，因此 timeout、connection、rate limit、refusal、
  truncation、content filter、schema 和输出门失败均不可达，不计入当前
  S4 验收或运行指标。
- 模板渲染或 admission 失败属于确定性实现或部署缺陷，必须 fail closed、
  告警，且不得自动回退到网络调用；状态机继续按既有事件推进，DM slot
  不得伪造成功。
- 未来 provider 重新进入后，任何 provider 或输出门失败都必须丢弃全部
  模型输出并回退 `template_text`；玩家可见路径不重试。

### S4-D5 信息边界

- 当前 S4 的模板输入只能是 public 或目标 seat 已授权的投影事实。
- 不传完整 `GameState`、原始事件 store、token、session、数据库对象、
  隐藏角色、狼队、药水、查验或未公开死因。
- 玩家发言只能作为 claimed statement，包装为
  `[玩家<seat_id>声称] <text>`，不能成为 fact。
- 原始 SPEAK event ID 和原文不得进入 template facts。
- 公共模板不得读取 seat-only facts；seat 模板不得读取其他 seat 的私密事实。

### S4-D6 模板输出完整性

- 模板目录只包含服务端冻结文本和允许的命名占位符。
- 外部输出、玩家输入和 provider 输出不能生成或修改最终文本。
- `final_text` 必须来自同一次模板解析和事实绑定结果。
- 缺失模板、未知占位符、未授权事实、Unicode 混淆、零宽字符、双向控制
  字符或玩家声称混入事实时必须 fail closed。
- 当前 S4 不存在 `prefix_text`、`suffix_text`、`claimed_refs`、
  `provider_status` 或 `raw_output`。

### S4-D7 测试与验收

- 模板路由、目录、渲染和 outbox 使用确定性单元/集成测试。
- 六浏览器 E2E 使用真实 S4 模板路径，不依赖外部网络。
- 公共、seat、audit 和 player replay 必须通过隐私回归。
- `LAT-001/004/005` 必须在真实 S4 模板路径重跑。
- 四路独立复核覆盖状态/outbox、信息隔离、模板失败关闭和确定性/延迟。
- 未执行的 provider 测试不得标记为通过或 deferred 以外的完成状态。

### S4-D8 不做边界

不做跨局记忆、向量数据库、LLM 裁决、LLM 改状态、自由聊天、多 provider
路由、公开半成品流式输出或模型驱动的玩家推理。

## 4. 权威与责任

| 能力 | 最终责任方 |
| --- | --- |
| 规则、阶段、可见性和事实 | S1/S2 领域与投影 |
| intent 与 audience 选择 | `TemplateIntentSelector` |
| 模板文本与占位符 | `TemplateRegistry` |
| 事实绑定与 fail-closed 渲染 | `TemplateRenderer` |
| DM 消息解析 | `TemplateDMService` |
| 发布顺序与去重 | `RoomActor` |
| trace 与指标 | `DMTemplateMetrics` |
| 公开渲染 | stage/player 前端 |

provider、LLM 和 OutputGate 不在当前责任表内。

## 5. 信息纪律

### 5.1 公共模板

公共模板只能包含：

- 已公开阶段与 revision；
- 公共死亡、放逐、无人出局等公开公告事实；
- 已公开投票聚合；
- 允许公开的动作提示和计时文本；
- 规则模板解释。

不能包含角色、狼队、药水、查验、未公开死因和原始玩家发言。

### 5.2 Seat 模板

Seat 模板只允许该 seat 合法可见的事实，并绑定当前 `(room_id, seat_id,
session_id)`。seat 变化后旧 seat-targeted 模板消息不得发布到新 session。

### 5.3 玩家声称

玩家声称只能作为独立 claimed statement 输入分析，不得进入模板 facts，
不得被改写为规则事实，不得出现在公共 DM 文本中。

## 6. 模板目录与渲染

- Catalog 版本化、只读、确定且可在测试中完整枚举。
- 每个 intent/channel/style 组合只允许一个匹配的 template variant。
- 同一事实、intent、audience 和 catalog version 必须生成相同字节。
- 模板占位符只能读取声明过的 fact 字段。
- 最终文本不能追加目录之外的文字。
- 失败时返回明确 `TemplateRenderError`，不发布占位、空串、fallback 或
  网络结果。

## 7. 延迟与可用性

- 模板路径不得依赖 network、API key、provider health 或外部进程。
- `dm.message` 必须在 2.0 秒内被既有 outbox 接纳；这是绝对上限，不是
  正常目标。
- `LAT-001` 要求模板入队 p95 `< 200ms`。
- `LAT-005` 要求模板 fail-closed 路径 `< 500ms`。
- 1.5 秒取消、1.6 秒 fallback 和 50% runtime hit-rate 是未来 provider
  重新进入门禁，不属于当前运行预算。

## 8. 指标与 Trace

当前模板路径记录：

- eligible 模板 slot；
- template admitted；
- render failed；
- slot suppressed；
- admission latency；
- source revision 和 category。

当前不记录 provider hit/fallback/stale、cold/warm、model 或 raw output。

Trace 不得包含隐藏角色、狼队、药水、查验、未公开死因、token、session、
原始玩家发言或原始事件 store。Player replay 不返回 `dm_trace`。

## 9. 暂停、重连和终局

- 暂停期间不产生新的 DM slot；已解析但未接纳的模板 slot 必须在 admission
  前复检 revision、phase 和 room state。
- seat 重连绑定新 session，旧 session 的 seat-targeted 模板消息失效。
- 公共模板不因 seat 重连失效。
- `GAME_ENDED` 模板固定；进入终局前，更早 announcement slot 必须先完成。
- 终局冻结后不得发布被更早 slot 取代的模板消息。
- 所有发布前检查必须在 RoomActor 顺序内原子完成。

## 10. Provider 重新进入

Provider 重新进入不是当前 S4 的隐式选项。必须另立版本化任务和规格：

1. 重新选型 2 到 3 个候选，冷/热分组记录样本、硬件、网络、prompt
   version、region 和失败原始计数。
2. 硬门禁仍为 `p99 < 1.2s`、`maximum < 1.5s`、provider failure `< 5%`、
   spike acceptance `>= 50%`；当前两个 DeepSeek 结果只作为失败证据。
3. 冻结 provider、model、endpoint 类型、transport、预算和回滚开关；
   保持单 provider、public-only、最多一次、无玩家可见重试。
4. 重建 active `LAT-002/003`、fake provider、真实 DMService、六客户端、
   隐私、pause/reconnect/terminal invalidation 和命中率低于 50% 回到用户
   决策的验收行。
5. 完成独立复核、用户接受和版本冻结后，才能在默认关闭的开关下接入生产
   路径；失败时立即回到模板，不改变状态机。

降低任一门禁属于产品决策，必须提高规格版本并重新获得用户确认。

## 11. 验收原则

S4 完成必须同时满足：

- 所有 active 模板 ID 有明确 owner 和实际命令证据；
- 模板路由、目录、渲染、outbox、恢复和日志隐私通过；
- 六客户端 E2E 在无 provider 网络下通过；
- 公共、seat、audit 和 player replay 无越权信息；
- 所有 fail-closed 路径先 Red 后 Green；
- 四路独立只读复核通过；
- 未验证的 provider、模型、硬件或延迟不被声称为已完成。

Deferred 行不得计入当前完成率。

## 12. 决策冲突规则

本文件与 S4 design 是 `system-design@1.1.0` 的版本化 S4 收窄覆盖：

- 不收窄或改写 S1/S2/S3 的非 S4 行为；
- 不放宽任何权限；
- 只进一步限制 S4 的模板输入、输出、trace 和延迟；
- 当前 S4 不启用 provider/LLM。

如果冲突，优先级为：

1. 用户最新明确决策；
2. 基础产品宪法；
3. 本 S4 宪法；
4. S4 design；
5. RulePack / S1 / S2 / S3 冻结规格；
6. implementation plan。

## 13. 变更规则

任何 S4 规则、权责、延迟、上下文、provider 或验收边界变化必须：

1. 记录旧决策和替换原因；
2. 更新验证矩阵、design 和 plan；
3. 增加对应失败回归；
4. 进行独立复核；
5. 提升版本号并填写 `supersedes`；
6. 经用户接受后才能冻结。

## 14. Changelog

### v1.1.0 - 2026-09-27

- 根据 S4-P0-01a/b 将当前 S4 收窄为 template-only。
- Provider/LLM 条款改为 deferred，并增加明确的重新进入条件。
- 删除 provider schema、1.5 秒取消、cold/warm 和真实 provider 完成条件
  对当前 S4 的约束。
- 增加模板 fail-closed、零网络、trace 最小时点和模板专用指标。

### v1.0.0 - 2026-09-26

- 固化 D1-D8 的 provider-neutral 路线、模板优先、信息纪律、输出门、
  降级和验收原则。
- 明确 public/seat context 与 host LLM 拒绝边界。
