---
spec_id: s3-frontend-constitution
version: 0.1.0
status: proposed
proposed_at: 2026-09-25
owner: product
depends_on:
  - product-constitution@1.1.0
  - system-design@1.1.0
  - s2-realtime-interface-design@1.2.0
supersedes: null
---

# S3 前端产品宪法提案 v0.1

## 1. 状态与效力

本文件是 S3 的前端产品宪法提案，状态为 `proposed`。它记录已经由用户确认的
五项决策及其精确边界，但在用户复核本文件并明确接受之前：

- 不得标记为 `frozen`。
- 不得据此编写冻结设计或实施计划。
- 不得创建 `frontend/` 代码、依赖或构建配置。

本文件只约束 S3；基础产品宪法、S1 领域边界和 S2 网络协议继续有效。若本文件
与更高层规格冲突，以基础产品宪法、S1/S2 冻结规格和用户最新明确决策为准。

## 2. S3 目标

S3 的可玩目标是让 6 名真人玩家在局域网内通过浏览器完成一局：

- 每名玩家使用自己的手机加入房间并操作自己的座位。
- 共享屏只展示公共舞台，不持有座位、主持人或审计权限。
- 主持人使用自己的手机或平板控制暂停、恢复和诊断入口。
- 所有规则状态仍只由后端 `GameCore.submit()` / `GameCore.tick()` 改变。
- 前端只根据服务端授权投影渲染，不自行推断规则、票型或隐藏信息。

## 3. 五项冻结决策

### 决策 1 - 手机玩家闭环优先

实现顺序先证明玩家手机端可以完成：

1. 加入和重连。
2. 角色揭示与确认。
3. 夜间行动。
4. 白天发言。
5. 普通投票、PK 和弃票。
6. 终局和玩家回放。

共享屏的公共舞台必须与玩家端共用协议层，但不能阻塞玩家闭环；视觉和音效完善
排在手机闭环之后。

前端目录保留三个独立功能边界：

```text
frontend/src/features/player/
frontend/src/features/stage/
frontend/src/features/host/
```

当前只先完成 player，但共享协议、路由和会话层不得硬编码成只有 player 可用。

### 决策 2 - 主持人手机控制，共享屏只读

- 主持人控制台运行在主持人自己的手机或平板。
- 共享屏是第三种只读客户端，不能使用 host token。
- 共享屏没有 display 会话时应显示未配对状态，不得降级为持有 host token。
- 共享屏不能发送游戏命令、主持人命令或订阅 `host.control`。
- 完整 host audit 是独立的 Bearer REST 授权面，不进入共享屏。

### 决策 3 - S3-P0 限定协议范围

S3-P0 是实现 S3-01 之前独立的后端协议预检任务，不占用 S3-01 至 S3-13 编号。

S3-P0 只允许以下范围：

1. 新增只读 `display` 会话。
2. 在纯状态投影中加入公共 `paused`。
3. 在 WebSocket 传输 envelope 中加入 `server_time`。
4. 在活动投票投影中加入安全的提交进度。

S3-P0 明确不做：

- 主持人纠错。
- 持久化。
- display 的 replay 或 audit 导出。
- display token 跨房间复用。
- 共享屏写操作。
- S4 AI DM 或 LLM 接入。

### 决策 4 - 主持人纠错延后

S3 不实现 `HOST_PATCH`、`HOST_REWIND_TO_SNAPSHOT` 或
`HOST_FORCE_TEMPLATE` 的状态修改功能。

主持人控制台必须明确显示：

```text
主持人纠错尚未实现，请结束并重开一局。
```

不得制作点击后无效果的假按钮。未来独立切片命名为
`S3-R Host Recovery` 或 `Post-S3 Host Recovery`，不得命名为 `S2-R`。

### 决策 5 - Tailwind 只作为样式实现层

- 设计 token 的真相是 `src/ui/tokens.ts` 与导出到 CSS variables 的 token。
- Tailwind 4 通过 `@theme` 消费这些 token。
- 组件不得散落 `bg-gray-800` 等一次性语义 class。
- 不使用 `dark:` 表示游戏昼夜。
- 定义显式 `day:` / `night:` 自定义变体，并绑定 `data-phase="day|night"`。
- 操作系统主题偏好不属于 S3 范围。

## 4. 身份与视图隔离

| 客户端 | 认证身份 | 默认频道 | 可见数据 | 可发送消息 |
|---|---|---|---|---|
| 玩家手机 | `actor_type=seat` | `public + seat` | `PublicView`、自己的 `SeatView` | `ping`、合法的 public/seat 订阅、自己的 `CommandEnvelope` |
| 共享屏 | `actor_type=display` | `public` | 仅 `PublicView` | `ping`、幂等 public 订阅 |
| 主持人手机/平板 | `actor_type=host` | `public + host.control` | `PublicView`、`HostControlView` | `ping`、合法 host 命令；审计走独立 REST |

补充约束：

- seat token 不能读取 host control、其他座位或 audit。
- display token 不能提交命令，不能订阅 seat/host.control，不能调用 replay/audit。
- host token 不能代替 seat token 完成玩家私有动作。
- host audit 只能由 host Bearer token 读取，不能通过公共 WebSocket 广播。

## 5. S3-P0 协议边界

### 5.1 display 会话

`display` 是新的房间级只读 actor 类型，不是 UI 角色别名。

身份要求：

- `actor_type = "display"`。
- token 绑定单个 `room_id`。
- token 不能复用为 seat 或 host。
- 每个房间只有一个当前有效的 display token。
- token 到期时间不得超过 `min(签发时间 + 1 小时, 房间到期时间)`。

配对要求：

- 主持人通过已认证的 host 会话主动发起配对。
- 服务端生成 6 位数字的一次性 pairing code。
- pairing code 只对同一房间有效，TTL 为 5 分钟。
- pairing code 成功交换一次后立即失效。
- pairing code 连续失败 5 次后失效；失败尝试按房间和客户端来源限流。
- 交换成功后服务端返回 display token；共享屏只在 `sessionStorage` 保存它。
- 主持人可以轮换或撤销 display token。
- 轮换后旧 token 立即失效；旧共享屏连接必须被关闭且不能收到后续公共更新。
- 同一 token 重连时，新连接替换旧连接，使用与 seat/host 相同的旧连接替换语义。

WebSocket 权限：

- 认证成功只加入 `public` 频道。
- `session.ready.snapshot.host_control` 必须为 `null`。
- `session.ready.snapshot.seat_view` 必须为 `null`。
- `command` 消息返回 `ACTOR_NOT_AUTHORIZED`，不得进入 `GameCore.submit()`。
- seat/host.control 订阅返回 `CHANNEL_FORBIDDEN`。
- 公共订阅是幂等的，只发布当前 `PublicView`。
- display token 不能用于 `/replay` 或 `/audit`。

如果 S3-P0 未完成，共享屏功能保持未启用；不得用 host token 加前端遮挡模拟
权限隔离。

### 5.2 公共暂停状态

`paused` 是由纯状态投影产生的公共事实：

- `PublicView.paused` 必须与 `GameState.paused` 一致。
- 主持人暂停或恢复后，所有 public/seat/host 视图按既有广播路径更新。
- 玩家和共享屏在 `paused=true` 时冻结倒计时显示。
- `paused=true` 时玩家端不展示可提交动作；服务端 `legal_actions` 仍是最终
  授权来源。
- `paused` 不暴露暂停原因、主持人身份或纠错状态。

### 5.3 服务端时间

`server_time` 是传输层元数据，不是领域状态：

- 不加入 `PublicView`、`SeatView` 或其他纯投影对象。
- `ProjectState` / `project_public_view()` 不读取时钟。
- `server_time` 由 RoomActor 的注入 clock 在构造传输消息时生成。
- `SessionReadyUpdate`、`PublicViewUpdate`、`SeatViewUpdate` 和
  `HostControlUpdate` 都携带 `server_time`。

客户端倒计时规则：

```text
remaining_ms = deadline_at is None ? 0 : max(0, deadline_at_ms - server_time_ms)
local_deadline_ms = performance.now() + remaining_ms
```

- 每次收到新视图时按上述公式重算锚点。
- 倒计时使用单调时钟 `performance.now()`，不使用系统墙钟。
- 同一 phase/deadline/revision 内，偏移小于 250 ms 时不重锚，避免消息抖动。
- deadline、phase 或 revision 变化时立即重锚。
- `paused=true` 时不继续消耗剩余时间；恢复后的新 deadline 是权威值。
- 该算法允许单向网络延迟使初始剩余时间略微偏长，但不允许提前触发本地超时。
- 是否启用本地自动超时由设计阶段决定；无论是否启用，服务端 tick 仍是唯一
  超时裁决者。

### 5.4 安全投票进度

活动投票只暴露聚合进度：

- 仅 `DAY_VOTE` / `DAY_PK_VOTE` 且投票轮未关闭时可见。
- `eligible_count` 等于当前轮的合法选民数量。
- `submitted_count` 等于已经提交投票或弃票的合法选民数量。
- 同一选民替换投票只计数一次。
- `0 <= submitted_count <= eligible_count`。
- 不暴露提交者身份、投票目标或当前票型。
- 投票结束后，进度字段不再作为活动轮次展示；历史票型仍遵守 S1 冻结的公开
  事件规则。

### 5.5 生产与测试控制边界

- 生产 WebSocket 不接受 `__test_advance_clock` 或任何测试命令。
- 生产 `create_app()` 不注册 clock 控制路由。
- Playwright E2E 在 `frontend/e2e/support/test_server.py` 中单独组装生产 app，
  注入 `FrozenClock` 并添加仅测试进程可见的控制路由。
- 测试控制路由只绑定 loopback。
- 测试控制路由不得进入生产导入路径或生产 OpenAPI 文档。
- 生产应用不读取 `ENV=test` 来切换额外协议能力。

## 6. 前端架构边界

S3 采用单个 React SPA：

```text
frontend/src/
  app/          路由、错误边界、应用壳
  protocol/     REST/WS DTO 和严格消息解析
  realtime/     WebSocket、重连、心跳、连接状态
  session/      按房间和角色隔离的本地会话
  features/player/
  features/stage/
  features/host/
  features/replay/
  ui/           token、基础控件、弹层、倒计时
```

约束：

- 只有一套 REST/WS 协议实现。
- 不使用 Redux、Zustand、TanStack Query 或 XState；服务端快照是权威状态。
- `useReducer` 和 selector 只做展示状态归并，不复制规则状态机。
- 所有 `command_id` 使用 `crypto.randomUUID()`。
- WebSocket 使用原生 API 和显式指数退避；不得因组件 render 重建连接。
- 路由至少覆盖 `/`、`/join/:roomCode?`、`/play/:roomCode`、
  `/stage/:roomCode`、`/host/:roomCode` 和 `/replay/:roomCode`。
- `session.ready` 到达前不渲染角色、私密事实、legal actions 或主持人控制。

## 7. 夜间与私密信息规则

- 私密内容默认被遮罩，只有明确的本地揭示动作后才挂载到可见 DOM。
- 揭示动作结束后立即回盖并卸载私密节点。
- `pointerup`、`pointercancel`、`blur`、`visibilitychange` 和锁屏恢复都必须
  触发回盖。
- 刷新或重连只恢复当前授权状态，不自动展开角色、目标或查验结果。
- 玩家被动死亡后不新增私密行动事实。
- 共享屏永远不订阅 seat 频道，也不显示角色、药水、查验、狼队决策或目标。
- 浏览器不能阻止截图、拍照或肩窥；验收只证明服务端投影和页面生命周期不会
  主动泄漏，不宣称绝对防偷窥。

## 8. 设备与可访问性

- 手机端以 320 CSS px 宽度仍可重排为最低验收线。
- 触控目标至少 44x44 CSS px；任何重要动作不能依赖 hover。
- 主要动作使用 Pointer Events，并保留键盘等价操作。
- 倒计时显示不得只靠颜色区分。
- 暂停、断线、重连、连接替换和房间关闭必须有明确可见状态。
- 主持人控制台和回放页面必须保持可见焦点。
- 共享屏应支持全屏和 Screen Wake Lock；不支持时显示可恢复提示，不阻塞内容。
- 所有页面必须避免私密文本与公共文本在同一容器中重叠或串层。

## 9. 会话与重连

- seat/host token 按房间保存在版本化 localStorage key 中。
- display token 只保存在 sessionStorage；同一标签页刷新后可以恢复，关闭标签页、
  新建标签页或清除会话后必须重新配对。
- token 不进入 URL 查询参数、日志、错误消息或 analytics。
- 重连后以 `session.ready.snapshot` 为权威恢复点。
- `last_seq` 只保留为诊断和观察字段；当前没有事件 gap replay，客户端不得
  模拟缺失事件。
- 1001 空闲关闭可以自动重新认证。
- 4001 表示 token/房间失效，清除对应会话并要求重新加入或配对。
- 4003 表示同身份旧连接被替换，旧页面停止自动重连并提示设备接管。
- 1008 和 1013 使用退避重连；连续失败后显示人工重试入口。

## 10. S3-P0 验证要求

S3-P0 至少包含以下永久测试：

1. display token 只能订阅 `public`。
2. display command 返回 `ACTOR_NOT_AUTHORIZED` 且状态不变。
3. seat/host.control 订阅返回 `CHANNEL_FORBIDDEN`。
4. pairing code 房间绑定、5 分钟到期、一次性消费和失败限流。
5. host 轮换/撤销后旧 display token 和旧连接失效。
6. 同 token 重连替换旧连接。
7. `PublicView.paused` 与 `GameState.paused` 同步，且不泄露暂停原因。
8. 四类传输更新均含 `server_time`，纯投影对象不含 `server_time`。
9. 活动投票进度只含聚合计数，不泄漏身份或目标。
10. 生产 app 不存在测试 clock 控制路由或消息。

S3-P0 完成后必须重新运行：

- 后端全量 pytest；不得低于 `395 passed, 6 skipped`。
- latency marker；不得低于 `4 passed, 2 skipped`。
- 无缓存 ruff、format check 和 strict mypy。
- `scripts/verify-delivery.ps1`。
- 一轮 fresh-context 独立只读复核。

## 11. S3 整体验收原则

- 6 名玩家可以完成加入、角色确认、至少一夜、一天、投票和终局。
- 共享屏不出现任何私密角色、药水、查验、目标或主持人控制。
- 玩家刷新和断线后的状态与 `session.ready` 一致。
- 暂停对玩家和共享屏可见，恢复后不产生提前超时。
- 主持人纠错入口明确说明未实现，不产生可点击假功能。
- 320 CSS px 手机宽度无水平滚动、文字溢出或操作遮挡。
- E2E 使用测试组装服务器推进冻结时钟，不真实等待 20-40 分钟。
- 所有测试和控制数据不得进入生产协议面。

## 12. 非目标

S3 不包括：

- AI DM、LLM 提示词、语音或 TTS。
- 账号、匹配、支付、公网云部署和跨房间观战。
- SQLite 持久化、服务重启恢复和跨进程房间。
- 主持人纠错、回退和补偿事件。
- 9/12 人规则、警长、白狼王或其他扩展角色。
- 玩家语音识别、抢话检测和实时语音房间。
- 原生 App、PWA 安装和离线单人模式。
- 对截图、拍照或物理肩窥的绝对防护。

## 13. 变更规则

本提案通过用户复核后升级为 `v1.0.0 frozen`。任何后续变更必须：

1. 记录旧决策和替换原因。
2. 更新依赖规格和验证矩阵。
3. 增加对应的失败回归测试。
4. 提升版本号。
5. 重新进行独立只读复核。

未同步更新规格和验证矩阵的 S3 行为变更不得进入实现。

## 14. Changelog

### v0.1.0 - 2026-09-25

- 固化手机优先、主持人独立设备、共享屏只读、S3-P0 限定范围、纠错延后和
  Tailwind 作为样式实现层五项决策。
- 明确 `display` 是只读跨层 actor，并要求一次性配对和可撤销会话。
- 明确 `paused` 属于状态投影，`server_time` 只属于传输 envelope。
- 明确测试时钟控制不得进入生产 WebSocket。
- 明确游戏昼夜使用 `day:` / `night:` 显式变体，不使用 `dark:`。
