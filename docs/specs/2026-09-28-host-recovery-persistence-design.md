---
spec_id: host-recovery-persistence-design
version: 0.1.0
status: proposed
owner: product
created_at: 2026-09-28
supersedes: null
---

# 主持人纠错与最小持久化设计草案

## 1. 目标

本阶段补齐上线前两项 P1 能力：

1. 主持人纠错：让常见误操作、网络掉线导致的动作丢失和状态争议不必通过
   结束并重开一局解决。
2. 最小持久化：进程重启后恢复仍在进行中的房间、状态、事件和有效令牌，
   使局域网朋友局不会因笔记本休眠或误关进程直接丢失整局。

本草案只解决产品范围和架构边界，不授权实现代码。实现必须在本草案评审
通过后另立 TDD implementation plan。

## 2. 权威基线

本草案不重新定义以下已经冻结的规则：

- [产品宪法](2026-09-23-product-constitution.md)：MVP 必须包含结构化主持人
  纠错；纠错不能直接改数据库或删除原始审计。
- [RulePack v1](2026-09-23-rulepack-v1.md)：`HOST_PATCH` 允许项、暂停前置、
  副本校验、成功/失败事务和 revision 单调性。
- [系统设计](2026-09-23-system-design.md)：`application.host_recovery`、
  SQLite、快照、命令日志、事件日志、`HOST_REWIND_TO_SNAPSHOT` 和
  `HOST_FORCE_TEMPLATE` 的职责边界。
- [验证矩阵](2026-09-23-verification-matrix.md)：`REC-*`、`VIS-*`、
  `E2E-*` 的恢复与隐私验收。

## 3. 当前差距

### 3.1 主持人纠错

- `HostPatchCommand`、`HostRewindToSnapshotCommand` 和
  `HostForceTemplateCommand` 已有严格 Pydantic 契约。
- 三条命令目前统一返回 `HOST_RECOVERY_NOT_IN_S1`，前端显示
  “主持人纠错尚未实现，请结束并重开一局”。
- `PAUSED`、revision、audit、host control 和有序 outbox 已存在，可复用。
- 尚未实现状态副本校验、纠错前快照、补偿事件、纠错 diff、快照回退和
  主机审查界面。

### 3.2 最小持久化

- 规格要求 SQLite，但当前 `backend/src/werewolf_dm` 没有 SQLite 或持久化
  适配层。
- `RoomRegistry`、`RoomActor`、`GameCore`、令牌记录和 display pairing
  均为进程内状态。
- 进程退出后房间、令牌、事件和当前局全部丢失。
- 前端已经有 seat/host/display 会话刷新和重连逻辑，但服务端重启后没有
  可恢复的权威状态。

## 4. 方案比较

### 4.1 主持人纠错

| 方案 | 说明 | 结论 |
| --- | --- | --- |
| A. 领域命令 + 应用事务 | 新增 `application.host_recovery`，在暂停态复制 `GameState`，通过受限 patch 生成新状态和补偿事件，由 SQLite 事务提交 | 推荐 |
| B. 应用层直接修改 `GameState` | 绕过 `apply_command()` 和状态机 | 拒绝；违反产品宪法，无法保证不变量 |
| C. 完整 Event Sourcing + 历史分叉 | 任意 revision 重放和持久事件流分支 | 当前不做；超出 P1，成本明显过高 |

### 4.2 最小持久化

| 方案 | 说明 | 结论 |
| --- | --- | --- |
| A. SQLite snapshot + event log + token digest | 每局保存当前状态、事件、快照、令牌摘要和恢复记录，启动时重建 `RoomActor` | 推荐 |
| B. 只保存最新 JSON 快照 | 实现简单，但无法审计、回放或解释纠错前后 | 只可用于临时恢复，不满足审计 |
| C. 完整事件溯源 | 所有状态由事件流重建，支持任意时间点分叉 | 当前不做；现有规格明确不要求 |

### 4.3 推荐组合

采用 4.1-A 与 4.2-A：

- 领域层只负责验证 patch 和产生确定性的状态/事件结果。
- `application.host_recovery` 负责暂停要求、快照、diff、补偿事件和事务。
- `infrastructure.persistence` 负责 SQLite 存储，不进入 domain 依赖。
- `GameCore` 仍是运行时权威状态源；SQLite 是可恢复副本，不成为 domain
  依赖。

## 5. P1 功能范围

### 5.1 主持人纠错

P1 只实现冻结规格内已有类型：

- `SET_ALIVE`
- `SET_ROLE`，仅允许暂停且未终局
- `SET_POTION`
- `SET_VOTE`
- `SET_SEER_CHECKS`
- `SET_PHASE`，只允许回到当前阶段开始
- `HOST_REWIND_TO_SNAPSHOT`
- `HOST_FORCE_TEMPLATE`

纠错前置条件：

1. 房间必须处于 `PAUSED`。
2. 游戏不能已经 `GAME_END`。
3. 命令 actor 必须是 host。
4. patch 必须在枚举和类型白名单内。
5. 先在状态副本上应用，再验证全部领域不变量和投影。

成功时必须产生：

- 新状态和新 revision。
- 纠错前快照。
- 纠错 diff。
- 补偿事件。
- `HOST_CORRECTION_APPLIED` 或等价的 host-only 审计记录。
- 重建后的 public/seat/host 投影。

失败时：

- 原状态、revision、投影和 outbox 不变。
- 房间保持暂停。
- 记录 `HOST_CORRECTION_REJECTED` 审计记录。
- 不删除任何历史事件。

### 5.2 最小持久化

P1 需要持久化：

- 房间码、room_id、seed、rulepack_version、expires_at、last_activity_at。
- host/seat/display 令牌摘要、角色、座位、session_id 和到期时间。
- 当前 `GameState`、revision、event_count 和 event_log_digest。
- 已经落账的 `DomainEvent`。
- 阶段开始、暂停、纠错前和终局的 host-only 快照。
- 主持人纠错审计记录和拒绝记录。

进程启动时：

1. 打开 SQLite 并执行 schema migration。
2. 加载未过期房间和令牌记录。
3. 用持久化状态和事件重建 `GameCore`。
4. 重建 `RoomActor`、TimerScheduler 和订阅空集合。
5. 恢复未过期 display session，但不恢复旧 WebSocket 连接。
6. 启动 reaper 和 room actor。

客户端重连路径不变：刷新或新建连接仍通过 token + `session.ready`
恢复当前授权视图。

## 6. 建议数据模型

```text
rooms
  room_code PK
  room_id UNIQUE
  seed
  rulepack_version
  state_json
  revision
  event_count
  event_log_digest
  expires_at
  last_activity_at

room_events
  room_id
  revision
  event_id
  event_json
  PRIMARY KEY(room_id, revision, event_id)

room_snapshots
  snapshot_id PK
  room_id
  revision
  reason
  state_json
  event_count
  created_at

room_tokens
  token_digest PK
  room_id
  actor_type
  seat_id
  session_id
  expires_at
  revoked

host_recovery_audit
  record_id PK
  room_id
  command_id
  status
  patch_type
  before_revision
  after_revision
  before_state_json
  after_state_json
  diff_json
  reason
  created_at
```

Schema 只存 token digest，不存 token 原文。密码、API key、真实个人信息
不得进入数据库。

## 7. 事务边界

每个会改变状态的命令都必须在同一个 SQLite 事务中完成：

1. 写入新 `rooms.state_json`、revision 和 event digest。
2. 追加 `room_events`。
3. 写入必要快照。
4. 写入 host recovery audit（如果适用）。
5. 清理或更新相关 token / display pairing。
6. 事务提交。

任一步失败：

- 回滚数据库事务。
- 恢复内存中的旧状态副本。
- 不广播新 revision。
- 不增加 outbox 序号。
- 记录拒绝或内部错误，但不泄漏 token、隐藏角色或原始异常文本。

## 8. 重启恢复流程

```text
create_app lifespan
  -> open SQLiteRoomStore
  -> migrate schema
  -> load non-expired room + token records
  -> reconstruct GameCore from latest state + events
  -> reconstruct RoomActor and start actor loop
  -> start reaper
```

恢复失败时，单局必须 fail closed：

- 不创建半初始化房间。
- 不把损坏状态加载为可玩状态。
- 记录 room-scoped recovery error。
- 客户端收到安全错误并允许主机重新创房。
- 其他健康房间不受影响。

## 9. 前端边界

主持人控制台增加真实纠错入口，但不改变 display/seat 权限：

- host 只通过 host WebSocket 发送纠错命令。
- host token 不进入 URL。
- display/seat 永远收不到纠错详情、diff、快照或审计原文。
- 纠错必须先暂停；UI 显示当前 revision、待修正字段和确认原因。
- 成功后以最新 host control/public/seat 视图为准。
- 失败时保留输入并显示安全错误码，不显示服务端内部消息。
- `HOST_FORCE_TEMPLATE` 在当前 template-only 系统中只记录强制模板请求并
  重算当前模板消息，不声称启用了 provider。

## 10. 非目标

- 不做任意数据库编辑、任意状态 JSON 修改或 LLM 自动纠错。
- 不做完整 Event Sourcing、任意 revision 分叉或时间线编辑器。
- 不做多进程、Redis、Kubernetes、公网房间和云同步。
- 不做 AI 补位、9 人规则包、语音或 provider 重入。
- 不修改原始历史事件；历史事实只能通过快照恢复流程处理。

## 11. 验收矩阵

本设计进入实现计划前，至少需要覆盖：

- `REC-006` 快照回退不删除原始事件。
- `REC-007` 非法纠错保持暂停并返回错误。
- `REC-008` 纠错后终局可解释修正前后状态。
- `REC-009` 副本校验失败只记录 rejected，不写 applied，revision 不变。
- `REC-013` 纠错事务中途失败时全部回滚。
- `E2E-007` 主持人暂停纠错后所有客户端同步恢复。
- 进程重启后：seat/host/display token 重连、revision 连续、事件完整、
  `GAME_END` 前状态可继续。
- 数据库损坏或单局恢复失败不阻塞其他房间。
- token 原文、隐藏角色、private facts、raw events 不进入公共视图。

## 12. 待评审决策

以下决策需要用户明确后再进入 implementation plan：

1. `HOST_REWIND_TO_SNAPSHOT` 的 P1 语义是否只允许回到纠错前快照，
   还是允许选择任意历史快照。
2. SQLite 默认路径使用本地项目数据目录，还是运行时环境变量指定。
3. `HOST_FORCE_TEMPLATE` 在 template-only 系统中是否允许成为无副作用
   audit 记录，还是继续保持拒绝。
4. 快照回退后是否保留完整历史分段，还是只保留 host-only 恢复记录。

## 13. 建议实施顺序

1. 持久化骨架、schema 和 token/room 恢复。
2. `GameCore` 状态/事件恢复。
3. 主持人纠错领域 patch 和事务边界。
4. 快照、回退和强制模板。
5. 前端主持人纠错面板。
6. 重启恢复、跨层隐私和端到端门禁。

本阶段不直接进入代码实现。下一步应先评审第 12 节决策，再生成逐任务
TDD implementation plan。
