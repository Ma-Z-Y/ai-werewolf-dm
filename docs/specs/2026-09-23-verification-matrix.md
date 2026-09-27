---
spec_id: verification-matrix
version: 1.5.1
status: frozen
proposed_at: 2026-09-27
frozen_at: 2026-09-27
supersedes: verification-matrix@1.5.0
owner: quality
depends_on:
  - product-constitution@1.1.0
  - rulepack-v1@1.1.0
  - system-design@1.1.0
  - s3-frontend-constitution@1.1.0
---

# AI 狼人杀 DM 验证矩阵 v1.5.1

## 1. 测试原则

1. 先写失败测试，再写最少实现，再重构。
2. 测试只打在公开边界，不依赖私有实现。
3. 状态机、信息隔离和胜负判定必须先于 UI 完成。
4. LLM 质量测试与规则正确性测试分离。
5. 任何完成声明必须有当前轮次命令输出作为证据。
6. 生成者不能独自批准自己的结果。

## 2. 首批测试边界

| Seam | 公开接口 | 主要观察 |
| --- | --- | --- |
| 规则核心 | `GameState.apply(command, revision)` | 事件、下一状态、错误码、状态哈希 |
| 视图投影 | `project_public_view(state)` / `project_seat_view(state, seat_id)` | 字段和授权范围 |
| AI 输出门 | `validate_dm_output(context, raw_output)` | 通过、拒绝、模板降级 |
| 实时命令 | WebSocket `command` / `view.updated` | 幂等、重连、广播范围 |
| 主持人恢复 | `pause` / `patch` / `rewind` / `resume` | 不变量和审计完整性 |

## 3. 单元测试矩阵

### 3.1 状态转移

| ID | 场景 | 断言 |
| --- | --- | --- |
| `ST-001` | 6 个座位准备 | 进入 `ROLE_REVEAL`，角色分布正确 |
| `ST-002` | 所有角色确认 | 进入第 1 夜 |
| `ST-003` | 狼人目标一致 | 锁定目标并进入预言家阶段 |
| `ST-004` | 狼人目标不一致 | 保持 `NIGHT_WOLF` |
| `ST-005` | 狼人协商超时 | 本夜空刀并继续 |
| `ST-006` | 狼人击杀后预言家查验 | 查验结果不受死亡结算影响 |
| `ST-007` | 女巫使用解药 | 狼人击杀被取消 |
| `ST-008` | 女巫使用毒药 | 毒药目标加入死亡集合 |
| `ST-009` | 女巫同夜使用两瓶药 | 拒绝命令 |
| `ST-010` | 女巫重复使用解药 | 拒绝命令 |
| `ST-011` | 女巫重复使用毒药 | 拒绝命令 |
| `ST-012` | 死亡玩家夜间行动 | 拒绝命令 |
| `ST-013` | 非法阶段提交命令 | 返回对应 `CommandErrorCode` |
| `ST-014` | 每次成功命令 | revision 恰好增加 1 |
| `ST-015` | 仅剩一名狼人提交目标 | 提交立即锁定 |
| `ST-016` | 仅剩一名狼人未在 60 秒提交 | 本夜空刀并继续 |
| `ST-017` | 存活预言家 30 秒未查验 | 放弃查验并继续 |
| `ST-018` | 存活女巫 30 秒未行动 | 不使用药水并继续 |
| `ST-019` | 发言玩家 45 秒未结束 | 自动公开跳过 |
| `ST-020` | 普通投票 30 秒截止仍有未提交者 | 未提交者自动弃票 |
| `ST-021` | PK 投票 20 秒截止仍有未提交者 | 未提交者自动弃票 |
| `ST-022` | 角色确认 120 秒截止仍有未确认者 | 自动确认，角色卡仍可重看 |
| `ST-023` | 女巫本人是狼队击杀目标 | 解药选项禁用，只允许毒药或跳过 |
| `ST-024` | 女巫被击杀且解药未使用 | 不能自救；药水保有量不变但女巫死亡后不再可行动 |

### 3.2 投票与平票

| ID | 场景 | 断言 |
| --- | --- | --- |
| `VOTE-001` | 单一最高票 | 唯一目标放逐 |
| `VOTE-002` | 全员弃票 | 无人出局 |
| `VOTE-003` | 两名并列最高票 | 进入 PK |
| `VOTE-004` | 三名并列最高票 | 三人全部进入 PK |
| `VOTE-005` | PK 玩家尝试投票 | 拒绝命令 |
| `VOTE-006` | PK 后唯一最高票 | 唯一目标放逐 |
| `VOTE-007` | PK 后再次平票 | 无人出局 |
| `VOTE-008` | PK 后无人有效得票 | 无人出局 |
| `VOTE-009` | 玩家投自己 | 拒绝命令 |
| `VOTE-010` | 结算前重复投票 | 新投票替换旧投票，不增加有效票数 |
| `VOTE-011` | 结算后重复投票 | 拒绝命令，票型不变 |
| `VOTE-012` | 连续无出局日 | `discussion_cursor` 按冻结规则推进，下一日首发座位确定 |

### 3.3 胜负

| ID | 场景 | 断言 |
| --- | --- | --- |
| `WIN-001` | 最后一名狼人死亡 | 好人获胜 |
| `WIN-002` | 狼人数等于好人数 | 狼人获胜 |
| `WIN-003` | 狼人数大于好人数 | 狼人获胜 |
| `WIN-004` | 夜间胜负条件成立 | 死亡公告和 `game.ended` 的 `outbox_seq` 顺序正确 |
| `WIN-005` | 终局后提交命令 | 拒绝所有状态改变命令 |
| `WIN-006` | 放逐最后一名狼人 | 放逐公告的 `outbox_seq` 小于 `game.ended` |

### 3.4 信息隔离

| ID | 场景 | 断言 |
| --- | --- | --- |
| `VIS-001` | 村民公共视图 | 不含任何角色、药水和查验信息 |
| `VIS-002` | 预言家座位视图 | 只含自己的查验结果 |
| `VIS-003` | 女巫座位视图 | 只含自己的药水状态和本夜击杀目标 |
| `VIS-004` | 狼人座位视图 | 含狼队成员和狼队击杀决策 |
| `VIS-005` | 死亡玩家视图 | 不新增私密行动信息 |
| `VIS-006` | 公共 WebSocket 消息 | 不含私有事件字段 |
| `VIS-007` | 错误响应 | 不包含隐藏事实 |
| `VIS-008` | 日志导出 | 默认敏感字段脱敏 |
| `VIS-009` | 模板渲染输入 | 只含授权事实 |
| `VIS-010` | 使用 3 号令牌订阅 5 号频道 | 拒绝订阅 |
| `VIS-011` | 玩家请求主持人审计导出 | 拒绝访问 |
| `VIS-012` | 玩家导出回放 | 只含公共事件和该玩家既有私密事实 |
| `VIS-013` | 玩家请求 `dm_trace` 或快照 | 拒绝访问 |
| `VIS-014` | 3 号座位令牌提交 5 号座位动作 | 根据令牌派生 actor 并拒绝 |
| `VIS-015` | 命令信封或 payload 含额外字段 | Pydantic 严格模式在对应层级拒绝 |
| `VIS-016` | `SET_PHASE` 尝试跳向未来阶段 | 拒绝纠错，状态不变 |

### 3.5 幂等与并发生效

| ID | 场景 | 断言 |
| --- | --- | --- |
| `IDEM-001` | 同一 `command_id` 重复提交 | 只应用一次 |
| `IDEM-002` | 过期 `expected_revision` | 返回冲突，不改变状态 |
| `IDEM-003` | 两名玩家基于同一 revision 同时投票 | 一份接受、一份冲突；冲突方刷新后用新 `command_id` 重试，最终两票落账 |
| `IDEM-004` | 断线重连 | 返回最新授权视图 |
| `IDEM-005` | 重复管理员暂停 | 幂等 |

## 4. AI DM 输出测试

`v1.5.0` 起，本表只保留历史 ID 定义。当前 active/deferred 状态以
第 14 节为准；JSON、schema、provider 失败和模型改写行不再是当前
template-only 验收。

| ID | 场景 | 断言 |
| --- | --- | --- |
| `DM-001` | 合法 `DMRenderOutput` | phrase ID、fact refs、空 claimed refs 和 style 通过全部输出门并广播 |
| `DM-002` | 非法 JSON | 回退模板，不阻塞 |
| `DM-003` | Pydantic 字段缺失 | 回退模板 |
| `DM-004` | 引用未授权 fact id | 拒绝并回退 |
| `DM-005` | 出现未授权座位号 | 拒绝并回退 |
| `DM-006` | 出现错误票数 | 拒绝并回退 |
| `DM-007` | 公开频道直接指认隐藏角色 | 拒绝并回退 |
| `DM-008` | 公开频道泄露狼队或药水信息 | 拒绝并回退 |
| `DM-009` | 高频模板播报 | 不调用 LLM |
| `DM-010` | LLM 超时 1.5 秒 | 立即回退模板 |
| `DM-011` | provider 连续失败 | 模板继续推进状态机 |
| `DM-012` | 玩家发言包含提示注入 | 不能改变规则事实或调用状态命令 |
| `DM-013` | 全角座位号和角色名 | 规范化后仍被拒绝 |
| `DM-014` | 零宽字符插入角色指认 | 清理后仍被拒绝 |
| `DM-015` | 中文数字指认隐藏角色 | 归一后仍被拒绝 |
| `DM-016` | 使用同义角色表达泄露 | 别名归一后仍被拒绝 |
| `DM-017` | `prefix_phrase` 或 `suffix_phrase` 含座位号/数字 | 拒绝并回退 |
| `DM-018` | 事实句被模型改写 | 拒绝并回退；最终只使用服务端展开的 `prefix_text + template_text + suffix_text` |
| `DM-019` | `DMRenderOutput` 含额外字段、缺失或错误 `schema_version` | Pydantic 严格模式拒绝并回退 |
| `DM-020` | 构造 `DMRenderContext` | `forbidden_patterns` 只进入临时 prompt 构造；持久化 trace 只记录 pattern 数量/哈希或闭集拒绝原因，不记录 prompt 原文 |
| `DM-021` | phrase ID 或 phrase 文本违反 allowlist/事实约束 | 在结构化解析或 catalog 校验阶段拒绝 |
| `DM-022` | 玩家正常发言 | AI DM 不插话 |
| `DM-023` | 相同 template_variant_id 与事实再次选择模板变体 | 变体 ID、phrase ID 集合和文本完全一致 |
| `DM-024` | 相同意图和阶段但事实或观众不同 | 不安全缓存 key 不命中，不能复用 |

所有 `DM-*` 拒绝测试都必须同时验证：

- 客户端没有收到未过滤文本。
- 状态机仍能进入下一状态。
- `dm_trace` 记录了失败原因。

### 4.1 Template Context 测试

`v1.5.0` 起使用模板渲染输入替代 LLM ContextBuilder；`CTX-001..004` 的
当前断言以第 14.3 节为准。

| ID | 场景 | 断言 |
| --- | --- | --- |
| `CTX-001` | 构造公共 Context | 不包含任何角色、药水、查验、狼队、未公开死因或原始 SPEAK 事件 ID/文本 |
| `CTX-002` | 构造女巫 Context | 只包含女巫依法可见的击杀目标与药水事实；seat audience 恰好一个且绑定当前 session |
| `CTX-003` | 相同 state、events、intent、channel、audience、template_variant_id、phrase_catalog_version | 输出字节级相同，包括确定性 `context_id` |
| `CTX-004` | Context 中事实与模板不一致 | 构造失败，不调用 LLM |

## 5. 延迟测试

`v1.5.0` 起 `LAT-002/003` deferred；当前只要求 `LAT-001/004/005` 在真实
S4 模板路径重跑。

| ID | 场景 | 阈值 |
| --- | --- | --- |
| `LAT-001` | 高频模板播报 p95 | 小于 200 ms |
| `LAT-002` | 复杂播报 admission 最大值和 p99 | 从 slot trigger 到 resolved `dm.message` 被 RoomActor 发布队列接纳，均小于 2.0 s |
| `LAT-003` | LLM 无响应 | 1.5 s 取消；模板在 2.0 s 前被 RoomActor 发布队列接纳 |
| `LAT-004` | 6 个 WebSocket 客户端 | 状态广播 p95 小于 300 ms |
| `LAT-005` | 回退路径最坏耗时 | 模板 + 输出门 + RoomActor admission 不超过 500 ms |

测试环境、运行时、硬件、并发数、构建版本和 template catalog version
必须记录在模板报告中。provider 重新进入时，模型、prompt version、
region 和 endpoint transport 另按 `S4-RE-*` 要求记录。

## 6. 暂停、恢复与纠错测试

`v1.5.0` 起 `REC-010` deferred；`REC-011/012` 改为模板重算和旧
slot/pending admission 失效，不包含 LLM task。

| ID | 场景 | 断言 |
| --- | --- | --- |
| `REC-001` | 投票中途暂停 | 计时器和命令冻结 |
| `REC-002` | 暂停后重连 | 玩家看到相同 revision |
| `REC-003` | 恢复 | 全部不变量通过后继续 |
| `REC-004` | 修正存活状态 | 生成审计事件和差值 |
| `REC-005` | 修正药水数量 | 后续行动按新状态校验 |
| `REC-006` | 回退快照 | 原始事件不删除，产生恢复记录 |
| `REC-007` | 非法纠错 | 保持暂停并返回错误 |
| `REC-008` | 纠错后终局 | 回放可解释修正前后的状态 |
| `REC-009` | 纠错副本校验失败 | 写 `HOST_CORRECTION_REJECTED`，不写 `APPLIED`，revision 和状态不变 |
| `REC-010` | 暂停时 LLM 迟到返回 | 迟到响应失效且不得发布；若仍需播报，使用新生成或模板结果 |
| `REC-011` | 暂停在 `DAY_ANNOUNCE` 后恢复 | 重新生成或模板播报后正常进入 `WIN_CHECK` |
| `REC-012` | 回退快照 | 旧计时器、旧命令和旧 LLM 任务全部失效 |
| `REC-013` | 纠错成功事务写入中途抛出异常 | 状态、applied、diff、补偿和投影全部回滚 |
| `REC-014` | 修正预言家查验记录 | 通过 `SET_SEER_CHECKS` 应用并重建座位视图 |

## 7. Headless 回放测试

### 固定退出标准

- 生成 100 个固定 seed 对局。
- 每局至少有一名随机策略玩家、一名保守策略玩家和一名攻击策略玩家。
- 每局必须到达 `GAME_END`。
- 不允许负票数、重复死亡、死人行动或非法阶段跳转。
- 相同 seed 和命令序列回放两次，`state_hash` 完全一致。
- 公共事件投影和私密事件投影分别做可访问性校验。

### Golden 文件

固定以下样本：

- 首夜平安夜。
- 首夜一人死亡。
- 解药救人。
- 毒药独立致死。
- 两狼同杀与空刀。
- 2 人 PK 二次平票。
- 3 人 PK 二次平票。
- 放逐最后一名狼人。
- 狼人达到人数优势。

## 8. 端到端真人流程测试

| ID | 场景 | 断言 |
| --- | --- | --- |
| `E2E-001` | 6 名玩家加入 | 所有座位获得唯一令牌 |
| `E2E-002` | 角色揭示 | 私密信息不进入公共屏 |
| `E2E-003` | 完整一夜 | 行动顺序和结果正确 |
| `E2E-004` | 完整一天 | 发言、投票和平票流程正确 |
| `E2E-005` | 玩家刷新 | 恢复相同座位和状态 |
| `E2E-006` | 玩家断线重连 | 不重复行动，不丢票 |
| `E2E-007` | 主持人暂停纠错 | 所有玩家同步恢复 |
| `E2E-008` | 对局结束 | 胜负、回放和导出一致 |

### 真人验收

- 连续完成 3 局真人测试。
- 每局保存屏幕录像、事件日志和主持人问题清单。
- 没有严重规则错误。
- 没有手改数据库。
- 没有秘密泄漏。
- 至少 80% 测试者认为 AI DM 降低了主持负担。

## 9. 交付门禁

实现开始后，任何 Agent 声称任务完成前必须运行：

```text
1. format/lint
2. type check
3. unit tests
4. state-machine property tests
5. information-leak tests
6. DM output-gate tests
7. end-to-end smoke test
```

任一步失败：

- Agent 不能结束任务。
- 必须返回失败输出。
- 必须记录失败样本和修复尝试。

Codex 环境下使用项目脚本和交付前检查等价实现，不依赖 Claude Code Stop Hook。

## 10. 独立评估

### 规划者

- 只生成规格、任务拆解和验收标准。
- 不实现代码。

### 生成者

- 按 spec 完成一个可独立验证的工作切片。
- 不批准自己的工作。

### 评估者

- 使用全新上下文。
- 读取原始需求和实际 diff。
- 运行确定性检查。
- 核查隐藏信息、规则错误、延迟、降级和恢复。
- 只输出通过或阻塞，并给出文件、行号、复现命令和证据。

## 11. AI 主持人审查清单

每局真人测试结束后检查：

- 是否出现未公开角色、狼队或药水信息。
- 是否出现错误票数、错误死亡或错误阶段。
- 是否存在超过 2 秒的播报。
- 是否有格式错误但未降级。
- 是否有玩家误解当前操作。
- 是否有重复、机械或影响节奏的播报。
- 是否需要新增正则、事实模板或提示词规则。

新增问题必须转成一个固定回归样本，不能只修改提示词后口头认为解决。

## 12. 完成定义

一个功能只有同时满足以下条件才算完成：

- 对应规格已存在。
- 失败测试先于实现存在。
- 实现使测试通过。
- 状态哈希或事件断言可复现。
- 信息隔离测试通过。
- 相关 AI 输出有降级路径。
- 独立评估者未发现阻塞问题。
- 规格、测试和实际行为一致。

## 13. S3 Frontend And Protocol Preflight

| ID | 场景 | 断言 |
| --- | --- | --- |
| `S3-P0-001` | display token 订阅 | 只获得 public；seat/host.control 返回 `CHANNEL_FORBIDDEN` |
| `S3-P0-002` | display 命令 | 返回 `ACTOR_NOT_AUTHORIZED`，状态和 revision 不变 |
| `S3-P0-003` | display 配对 | cross-room 拒绝；5 分钟到期；服务端返回 expires_in_seconds；5 次失败失效；来源限流为 429 |
| `S3-P0-004` | display 轮换 | 同 token 重连为 4003；轮换旧 session 为 4001；新 session 存活 |
| `S3-P0-005` | 公共暂停 | `PublicView.paused/paused_at` 与纯状态一致；不泄漏原因；暂停后刷新仍恢复冻结剩余时间 |
| `S3-P0-006` | 传输时间 | 四类 WS update 都含同一采样 `server_time`；纯投影不含该字段 |
| `S3-P0-007` | 投票进度 | `DAY_VOTE`/`DAY_PK_VOTE` 只有 submitted/eligible 聚合计数 |
| `S3-P0-008` | 公共时间线 | 只收公开事件；平安夜/平票/超时/暂停/恢复/终局均为中文稳定文案；不改变 state hash |
| `S3-P0-009` | 生产测试面 | production app 的 OpenAPI、路由和实际请求均无 `/__test__` |
| `S3-E2E-001` | 六浏览器真实对局 | 6 player + host + stage 使用动态 room code 到达 `GAME_END`，覆盖 PK、暂停、重连和截图 |
| `S3-A11Y-001` | 320x568 | 主要玩家状态无水平溢出，可见按钮/链接/role=button 目标至少 44x44 |

## 14. S4 AI DM Template-Only Verification

本节是 `v1.5.1` 冻结的 template-only 验证增量。状态只允许：

- `completed`：证据已固定，不表示门禁通过；
- `template-only`：当前 S4 必须实现和验收；
- `deferred to future provider re-entry`：当前不实现、不验收、不计入完成；
- `removed`：不再属于任何当前计划。

### 14.1 Active 与已完成验证

| ID | 状态 | 场景与断言 |
| --- | --- | --- |
| `S4-P0-001` | completed | provider spike 已执行；两个候选失败，`selected_provider=none` |
| `S4-P0-002` | completed | 已冻结 template-only、未来 1.5s/1.6s/2.0s/50% 重入门禁和失败证据 |
| `S4-OFF-001` | template-only | 所有 active intent 均为 template，零 provider job、零网络、零凭据、零 `llm_eligible` |
| `S4-TEMPLATE-001` | template-only | catalog 覆盖完整；SHA-256 canonical variant key 确定且 audience 顺序无关；相同输入产生相同字节和 source-event 映射 |
| `S4-DM-025` | template-only | `final_text` 精确等于服务端渲染结果，且只出现一次 |
| `S4-DM-026` | template-only | confusable、零宽、双向控制、全半角和中文数字不能绕过 catalog/renderer |
| `S4-DM-027` | template-only | 玩家声称不进入 facts 或最终文本；原始 SPEAK ID/文本被拒绝 |
| `S4-DM-028` | template-only | 每个 eligible slot 记录 admitted/render_failed/suppressed 终态、admission latency 和双序列守恒 |
| `S4-DM-029` | template-only | 缺模板、非法 catalog、未授权事实、`NO_FACTS_USED`、stale/timeout admission 必须 Red-first fail closed |
| `S4-DM-030` | template-only | 隐藏角色、狼队、药水、查验和未公开死因不得通过 catalog 或 renderer 泄漏 |
| `S4-DM-031` | template-only | 当前输出没有 `claimed_refs`；claimed statement 不得被改写为事实 |
| `S4-DM-032` | template-only | provider 硬关闭时，任何 intent/channel 都不能产生 `llm_eligible` |
| `S4-TRACE-001` | template-only | host audit 只导出 clipped template trace；player replay 不导出 `dm_trace` |
| `S4-HOST-001` | template-only | 不构造 host template context；任何 host/seat provider 扩展只能 deferred |
| `S4-LAT-001` | template-only | slot 带 `trigger_at_monotonic_ms` 与 `trigger+2000ms` deadline；`now == deadline` 可接纳，`now > deadline` fail closed 并记录 `admission_timeout` |
| `S4-REC-001` | template-only | pause/revision/phase/room/session/slot 变化后旧模板消息不得发布或重复发布 |
| `S4-OUT-001` | template-only | RoomActor 按领域 seq 升序 admission；`game.ended` 不得越过更早 `dm.message` |
| `S4-OUT-002` | template-only | 领域 announcement seq 与客户端 `outbox_seq` 分离且可映射 |
| `S4-MET-001` | template-only | 模板终态互斥、domain/transport mapping 守恒；记录 max lag/ratio；无 provider 指标 |
| `S4-ACC-001` | template-only | 四路使用明确检查清单；覆盖 provider 域名请求为 0、固定 seed 两次 `dmMessages` deep equal |

`LAT-001/004/005` 虽有 S2 基础证据，仍必须在真实 S4 模板路径重跑。
`S4-TEMPLATE-001` 还要求 `TemplateRenderResult.unused_fact_ids` 排序且
无重复；传入非空 facts 但均未使用时以 `NO_FACTS_USED` 失败关闭。

### 14.2 Deferred Provider Verification

| ID | 状态 | 原因 |
| --- | --- | --- |
| `S4-PROV-001..003` | deferred to future provider re-entry | 当前无 provider result、raw output 或 schema |
| `LAT-002/003` | deferred to future provider re-entry | 当前不存在 provider p99、1.5s cancel 或 1.6s fallback |
| `DM-002/003/010/011/018/019/020` | deferred to future provider re-entry | 仅适用于模型输出、provider 失败或 raw trace |
| `REC-010` | deferred to future provider re-entry | 当前不存在迟到 LLM 响应 |

Deferred rows 不得计入当前 S4 完成率，也不得用 P0-01a/b 结果替代。

`S4-DM-028/031/032` 只保留第 14.1 节列出的模板断言。未来 provider
重入时，provider 专属语义必须使用新的 `S4-RE-*` ID，不能重新挂回这些
已完成状态定义的 ID。

### 14.3 既有验证债归属

| 验证 ID | 状态 | owner | 当前证据 |
| --- | --- | --- | --- |
| `DM-001`、`DM-004..009` | template-only | `S4-01`、`S4-02`、`S4-03` | 模板输出、授权 facts、隐藏事实和模板路径测试 |
| `DM-013..017`、`DM-021..024` | template-only | `S4-01`、`S4-02` | catalog、Unicode、determinism 和 claim 隔离 |
| `CTX-001..004` | template-only | `S4-02` | 模板渲染输入授权、session、确定性和 mismatch fail closed |
| `VIS-009` | template-only | `S4-02`、`S4-05` | template input allowlist、trace 裁剪和审计导出 |
| `LAT-001/004/005` | template-only | `S4-04`、`S4-05`、`S4-06` | 模板 enqueue、六客户端广播和 fail-closed path |
| `REC-011/012` | template-only | `S4-04`、`S4-06` | 恢复后模板重算和旧 slot/pending admission 失效 |

### 14.4 活跃 Owner 映射

| 验证 ID | owner 任务 | 预期工件 |
| --- | --- | --- |
| `S4-OFF-001` | `S4-01`、`S4-03`、`S4-06` | import/network guard、service 测试和 E2E 无 provider 证据 |
| `S4-TEMPLATE-001` | `S4-01`、`S4-02` | catalog/variant/renderer 测试 |
| `S4-DM-025..032` | `S4-01`、`S4-02`、`S4-03`、`S4-05` | 模板渲染、失败关闭、指标和隐私测试 |
| `S4-TRACE-001`、`VIS-009` | `S4-05` | audit/replay/log 隐私测试 |
| `S4-LAT-001`、`LAT-001/004/005` | `S4-04`、`S4-05`、`S4-06` | outbox、浏览器和性能证据 |
| `S4-REC-001`、`REC-011/012` | `S4-04`、`S4-06` | stale/reconnect/recovery 测试 |
| `S4-OUT-001..002` | `S4-04` | 双序列和终局顺序测试 |
| `S4-MET-001` | `S4-05` | 模板终态守恒测试 |
| `S4-ACC-001` | `S4-06` | 四路独立审核工件 |

### 14.5 Provider 重新进入

未来 provider 重新进入必须新增独立 `S4-RE-*` 任务，并恢复至少：

- 2 到 3 候选、冷/热样本、硬件、网络、prompt version 和失败原始计数；
- `p99 < 1.2s`、`maximum < 1.5s`、failure `< 5%`、
  spike acceptance `>= 50%`；
- provider result envelope、public-only context、strict output gate、
  timeout/refusal/truncation/schema 失败关闭；
- fake provider、真实 DMService、`LAT-002/003`、runtime hit/fallback 和
  reconnect/terminal invalidation；
- 独立复核和用户接受后的版本化规格修订。

降低门禁属于产品决策，不能由实现者自行修改。

## 15. Changelog

### v1.5.1 - 2026-09-27

- 增加 canonical SHA-256 variant key 和 audience 顺序无关断言。
- 增加 `unused_fact_ids` 与 `NO_FACTS_USED` 失败关闭。
- 增加 slot trigger/deadline 与 `admission_timeout`。
- 增加 domain/transport max lag、ratio 和映射守恒。
- 增加 provider 域名请求为零及固定 seed 字节级 `dmMessages` E2E。
- 明确四路独立复核清单。

### v1.5.0 - 2026-09-27

- 根据 `S4-P0-01a/b` 将 active S4 验证收窄为 template-only。
- 新增 `S4-OFF-001` 和 `S4-TEMPLATE-001`。
- 将 provider result、LLM output、1.5s cancel、runtime hit/fallback 和
  cold/warm 指标标记为 deferred。
- 保留模板渲染、隐私、outbox、恢复、六客户端和确定性延迟验证。
- 明确 deferred 行不计入当前 S4 完成率。

### v1.4.0 - 2026-09-26

- 增加 S4 provider 选型、预算冻结、事实句精确保留、Unicode 防篡改、
  玩家声称隔离、指标完整性、逐任务降级回归和四路独立验收。
- 独立复核后补充 provider 结果信封、2.0s outbox 终点、静态 forbidden
  patterns、claimed refs、seat audience/session、reconnect/generation
  和指标公式。
- S4 规格已通过三轮 fresh-context 复核并冻结。

### v1.3.0 - 2026-09-26

- S3-P0-005 增加 `paused_at` 纯投影与暂停期间重新挂载后的冻结计时回归。
- S3-P0-003 增加服务端相对 TTL，避免客户端时钟偏差误判配对码。

### v1.2.0 - 2026-09-25

- 增加 S3-P0 display、配对、时间、投票、public timeline 和生产测试面测试。
- 增加六浏览器 E2E 和 320x568 布局/触控验收。

### v1.1.0 - 2026-09-23

- 新增女巫不可自救和药水状态测试。
- 新增 Context Builder 纯函数与可见性测试。
- 新增预防性约束、沉默策略、模板变体和安全缓存测试。
- 遗言阶段和竞价发言未纳入 v1.1，因此没有新增实现型测试。
