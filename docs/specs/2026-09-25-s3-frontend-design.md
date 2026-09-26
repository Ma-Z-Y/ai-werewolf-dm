---
spec_id: s3-frontend-design
version: 1.1.0
status: frozen
proposed_at: 2026-09-25
frozen_at: 2026-09-26
owner: engineering
depends_on:
  - product-constitution@1.1.0
  - system-design@1.1.0
  - verification-matrix@1.3.0
  - s2-realtime-interface-design@1.2.0
  - s3-frontend-constitution@1.1.0
supersedes: null
---

# S3 前端与协议预检设计 v1.1

## 1. 目标与边界

本设计把冻结的 S3 产品宪法展开为可实现的接口、状态流、文件边界和验证策略。

S3 交付：

- 一个 React SPA，提供 player、stage、host 和 replay 四种路由视图。
- 一个独立的后端协议预检阶段 `S3-P0`。
- 测试组装服务器和 Playwright 六客户端端到端验证。

S3 不交付：

- AI DM、LLM、语音、TTS。
- 持久化、数据库、公网账号、匹配和支付。
- 主持人纠错和回滚。
- 共享屏写操作。
- 截图、拍照或物理肩窥的绝对防护。

## 2. 总体架构

```text
Browser
  |
  | REST: room/session/pairing/replay
  | WS:   auth -> session.ready -> authorized view updates/commands
  v
FastAPI interface layer
  |
  | RoomRegistry / TokenService / ConnectionSink
  v
RoomActor single-writer queue
  |
  | GameCore.submit() / GameCore.tick()
  v
S1 domain state and projections
```

约束：

- 前端只有一个协议实现。
- 仅 `GameCore.submit()` / `GameCore.tick()` 改变游戏状态。
- `display` 是只读传输身份，不进入领域命令。
- `server_time` 只存在于传输消息。
- 测试控制面只存在于测试组装 app。

## 3. 前端项目结构

```text
frontend/
  package.json
  pnpm-lock.yaml
  tsconfig.json
  tsconfig.app.json
  tsconfig.node.json
  vite.config.ts
  index.html
  src/
    main.tsx
    app/
      App.tsx
      routes.tsx
      ErrorBoundary.tsx
    protocol/
      models.ts
      parseServerMessage.ts
      errors.ts
    realtime/
      RoomSocket.ts
      useRoomSocket.ts
      reconnect.ts
    session/
      roomSession.ts
      storage.ts
    features/
      player/
        PlayerRoute.tsx
        JoinRoomScreen.tsx
        SeatShell.tsx
        RoleRevealSheet.tsx
        NightActionScreen.tsx
        DayDiscussionScreen.tsx
        VoteScreen.tsx
      stage/
        StageRoute.tsx
        StagePairingScreen.tsx
        StageShell.tsx
        StageTimeline.tsx
        StageTimer.tsx
      host/
        HostRoute.tsx
        HostControlScreen.tsx
        HostDiagnostics.tsx
        HostCorrectionNotice.tsx
      replay/
        ReplayRoute.tsx
        ReplayTimeline.tsx
    ui/
      tokens.ts
      tokens.css
      Button.tsx
      Dialog.tsx
      Timer.tsx
      ConnectionBanner.tsx
      PhaseBadge.tsx
  e2e/
    support/
      test_server.py
    player-flow.spec.ts
    stage-flow.spec.ts
    host-flow.spec.ts
```

`features/player`、`features/stage` 和 `features/host` 只通过 `protocol/`、
`realtime/` 与 `session/` 访问外部系统，不互相导入内部组件。

## 4. 技术选择

| 领域 | 选择 |
|---|---|
| 包管理 | pnpm 11.19.0 |
| 运行时 | Node.js 24.x |
| UI | React 19 + TypeScript + Vite |
| 路由 | `react-router-dom` |
| 样式 | Tailwind 4 消费项目 token |
| 图标 | `lucide-react` |
| QR | `qrcode` |
| 单元/组件测试 | Vitest + React Testing Library |
| E2E | Playwright |
| 状态 | `useReducer` + 纯 selector |

明确不引入：

- Redux、Zustand、Jotai、TanStack Query、XState。
- axios、socket.io、UI 组件库。
- `zod` 或第二套运行时 schema 库。

WebSocket JSON 由 `parseServerMessage.ts` 做单一判别联合解析。未知消息被忽略
并记录本地诊断，不进入应用状态。

## 5. 路由

| 路由 | 用途 | 会话 |
|---|---|---|
| `/` | 创房和加入入口 | 无 |
| `/join/:roomCode?` | 玩家输入房间码和名字 | 可预填 |
| `/play/:roomCode` | 玩家座位视图 | seat token |
| `/stage/:roomCode` | 共享屏公共舞台 | display token |
| `/host/:roomCode` | 主持人控制台 | host token |
| `/replay/:roomCode` | 玩家回放 | seat token |

路由守卫：

- 缺少认证会话时回退到 join 或 pairing。
- token 房间与 URL 房间不一致时清除 token，不发起 WebSocket。
- `/stage` 没有 display token 时只显示配对页。
- `/host` 没有 host token 时显示恢复或重新创房入口。
- `/replay` 使用 seat token；host audit 只通过独立授权入口打开。

## 6. 本地会话

localStorage key：

```text
werewolf:v1:room:<ROOM_CODE>:seat
werewolf:v1:room:<ROOM_CODE>:host
werewolf:v1:last-host-room
```

sessionStorage key：

```text
werewolf:v1:room:<ROOM_CODE>:display
```

存储结构：

```ts
export interface StoredSeatSession {
  schemaVersion: 1;
  roomCode: string;
  roomId: string;
  seatId: number;
  token: string;
  expiresAt: string;
}

export interface StoredHostSession {
  schemaVersion: 1;
  roomCode: string;
  roomId: string;
  token: string;
  expiresAt: string;
}

export interface StoredDisplaySession {
  schemaVersion: 1;
  roomCode: string;
  roomId: string;
  token: string;
  expiresAt: string;
}
```

读取失败、schema 不匹配、房间不匹配或已过期时删除对应会话。所有存储函数捕获
`Storage` 异常，不让隐私模式或 quota 错误影响游戏逻辑。

## 7. S3-P0 后端协议设计

### 7.1 Actor 扩展

在应用与传输边界统一使用：

```python
ActorType = Literal["seat", "host", "display"]
```

`AuthenticatedActor` 增加 `display` 形状校验：

```python
if self.actor_type == "display" and self.seat_id is not None:
    raise ValueError("display actor cannot carry seat_id")
```

`TokenRecord` 使用同一 actor union，display 记录的 `seat_id` 必须为空，并额外
携带 `session_id: UUID`。seat/host 记录的 `session_id` 为 `None`。

`ConnectionSink.bind_actor()` 的默认频道：

```python
if actor_type == "display":
    channels = frozenset({"public"})
elif actor_type == "seat":
    channels = frozenset({"public", "seat"})
else:
    channels = frozenset({"public", "host.control"})
```

`RoomSubscriber.actor_type` 和 `_subscriber_identity()` 同步扩展为
`seat | host | display`，并按 `(actor_type, seat_id, session_id)` 区分身份。

- seat 和 host 的 identity 继续是 `(type, seat_id, None)`。
- 同一 display token 重连的 `session_id` 相同，旧连接按现有替换语义收到
  `4003`。
- 轮换 token 产生新的 `session_id`，旧连接按失效语义收到 `4001`。

### 7.2 display 会话存储

`RoomRegistry` 增加 display 配对状态：

```python
@dataclass(slots=True)
class DisplayPairing:
    pairing_code_digest: str
    room_id: UUID
    expires_at: datetime
    failed_attempts: int = 0
```

`TokenService` 增加：

- `issue_display(room_id, ttl)`。
- `display_record(room_id)`。
- `revoke_display(room_id)`。

每个房间只有一个当前 display token。签发新 token 时删除同一房间旧 token，
使旧 token 无法重新认证。

`issue_display()` 必须先成功取得 raw token 并构造新 record，再原子替换
`_display_records[room_id]`；不得先撤销旧 record 再调用 token source。这样
source 失败不会丢失仍有效的旧会话。

`RoomActor` 提供同步的 `sync_display_session()`, 通过
`events.put_nowait(SyncDisplaySessionEvent(active_session_id=room.display_session_id))`
请求关闭不再匹配的 display 连接。该方法不直接修改 subscriber 集合，实际
关闭仍由单写者 `_run_loop()` 执行。

`RoomActor` 同时维护 `display_token_digest: str | None` 和
`display_session_id: UUID | None`：

- 成功签发 display token 后由 `RoomRegistry` 同时设置为新摘要和新 session id。
- 撤销时两者都设为 `None`。
- 已认证 display 消息循环每轮比较 `record.token_digest` 与该值，不一致就
  关闭 `4001`。
- 这样不需要把 `TokenService` 注入 WebSocket 传输层。
- 广播和显式 publish 在 offer 前再次比较 subscriber `session_id`；不匹配时
  直接 `request_close(4001)` 且不发送当前消息。不能只依赖之后处理
  `SyncDisplaySessionEvent`。
- `AttachSubscriberEvent` 在同一单写者循环内、加入 subscriber 和发送
  `session.ready` 前再次比较 display `session_id`。不匹配时只关闭连接并
  完成 attach future，不写 subscribers、不发送任何快照。

### 7.3 配对与撤销 REST

创建配对码：

```http
POST /rooms/{room_code}/display-pairings
Authorization: Bearer <host-token>
```

响应：

```json
{
  "pairing_code": "482913",
  "expires_at": "2026-09-25T09:05:00Z",
  "expires_in_seconds": 300
}
```

规则：

- 仅 host token 可调用。
- 新配对码替换同房间旧的未消费配对码。
- pairing code 为 6 位数字，服务端只保存 SHA-256 摘要。
- 固定 TTL 为 5 分钟。
- 响应同时返回由同一服务端时钟计算的 `expires_in_seconds`；客户端不得用
  浏览器墙钟直接解释 `expires_at`。

用配对码创建 display 会话：

```http
POST /rooms/{room_code}/display-sessions
```

请求：

```json
{"pairing_code": "482913"}
```

响应：

```json
{
  "room_id": "uuid",
  "display_token": "opaque-once",
  "expires_at": "2026-09-25T10:00:00Z"
}
```

规则：

- room code 与 pairing code 必须匹配同一房间。
- 过期、消费过或不存在的配对码统一返回 `TOKEN_INVALID`。
- 每次错误尝试增加 `failed_attempts`。
- 达到 5 次后配对码失效。
- 客户端来源和房间均参与限速：同一房间与来源每 60 秒最多 10 次交换尝试；
  第 11 次返回 `RATE_LIMITED`。
- 第 5 次错误配对本身仍返回 `TOKEN_INVALID` 并失效该配对码；“5 次失败”与
  “来源限速”是两个独立规则。
- 成功交换后配对码立即消费。
- display token TTL 为 `min(now + 1 hour, room.expires_at)`。

撤销当前 display 会话：

```http
DELETE /rooms/{room_code}/display-sessions/current
Authorization: Bearer <host-token>
```

规则：

- 删除当前 display token。
- 调用 `RoomActor.sync_display_session()` 投递
  `SyncDisplaySessionEvent(active_session_id=None)`。
- RoomActor 关闭全部 display subscribers，关闭码为 `4001`。
- 重复撤销幂等返回 204。

轮换通过 host 重新创建配对码、stage 交换新 token 完成。新 token 签发时旧 token
失效；交换成功路径设置新 `display_session_id` 并调用
`sync_display_session()`，由 RoomActor 只关闭 session id 不匹配的旧连接。

`RoomRegistry.remove_room()` 必须同时删除该房间的 pairing、display token 和
display subscriber；过期房间不能留下配对状态。

### 7.4 display WebSocket

认证：

- display token 通过 `/ws` 首帧 `auth`。
- `session.ready.snapshot.seat_view` 和 `host_control` 均为 `null`。
- 默认频道只有 `public`。
- 认证后、attach 前必须比较 `record.session_id` 与
  `room.display_session_id`；不一致时直接以 `4001` 关闭，不发送
  `session.ready`。

消息策略：

| 客户端消息 | display 结果 |
|---|---|
| `ping` | `pong` |
| `subscribe public` | 幂等；发布当前 public view |
| `subscribe seat` | `CHANNEL_FORBIDDEN` |
| `subscribe host.control` | `CHANNEL_FORBIDDEN` |
| `command` | `ACTOR_NOT_AUTHORIZED`，不进入命令队列 |

连接行为：

- 同 display 身份重连替换旧 subscriber，旧连接收到 `4003`。
- display token 轮换或撤销后，旧连接收到 `4001`。
- 房间过期或关闭时，display 按现有房间关闭路径收到 `4001`。
- 轮换测试必须证明旧连接在关闭前收不到任何后续 public update，并且新
  session 不会被旧 session 的关闭逻辑误伤。

实现说明：

- `ws.py` 必须在 `SeatSubscribeMessage` 之前读取通用 `channel`，并先验证它
  是字符串。
- display 的 `channel != "public"` 立即返回 `CHANNEL_FORBIDDEN`。
- host 的显式 `host.control` 订阅按幂等 no-op 处理，因为该频道已在认证时
  绑定；不新增第二份订阅状态。
- seat 的显式 `seat` 分支保留现有跨座位拒绝语义。
- 其他未知 channel 返回 `CHANNEL_FORBIDDEN`，不能静默忽略。
- `channel` 不是字符串时返回 `CHANNEL_FORBIDDEN`，不能执行集合成员测试。

### 7.5 公共暂停状态

`PublicView` 增加纯状态字段：

```python
paused: bool = False
paused_at: datetime | None = None
```

`project_public_view()` 和 `project_seat_view()` 都从 `GameState.paused` 和
`GameState.paused_at` 赋值。SeatView 继续继承 PublicView。

`paused_at` 是纯状态投影，不读取当前时钟。它只用于让新挂载或刷新后的
客户端恢复服务端冻结的剩余时间；暂停时剩余时间按
`deadline_at - paused_at` 计算，恢复后服务端平移 deadline 并清空
`paused_at`。

暂停和恢复仍通过 `GameCore.submit()` 产生 revision 和广播；前端不根据错误或
本地计时猜测暂停。

### 7.6 传输时间

四个 WS 输出模型增加：

```python
server_time: datetime
```

规则：

- `RoomActor` 构造每条输出时调用 `self.clock()`。
- `RoomSnapshot` 不增加 `server_time`。
- 同一事件广播到多个 subscriber 时使用同一个 `server_time` 样本。
- attach/current-publish 路径先取一次 `now`，同时用于 `touch(now)` 和消息
  `server_time`；不得在同一事件内重复读注入时钟。
- 单元测试通过注入固定时钟锁定精确值；E2E 使用测试进程内的
  `TestClock`。
- 显式 `subscribe` 的 `_publish_current_to()` 也必须接收同一个
  `server_time` 参数，不能使用默认值或再次读时钟。
- `interfaces/http_ws/models.py` 中用于严格测试的 `SessionReadyMessage` 和
  `PublicViewMessage` 必须与实际传输 DTO 同步增加 `server_time`；它们是形状
  镜像，不得形成第三种格式。

### 7.7 投票聚合进度

`VoteSummary` 增加：

```python
submitted_count: int = Field(ge=0)
eligible_count: int = Field(ge=0)
```

`_active_vote_summary()`：

- 非活动轮次仍返回 `None`。
- `eligible_count = len(round_.eligible_voter_ids)`。
- `submitted_count = len({vote.voter_seat_id for vote in round_.votes})`。
- 同一选民替换投票只计一次。
- `tallies` 在活动轮次继续为空。
- 不输出 voter id、target id 或落后/领先状态。

### 7.8 测试控制边界

生产包不增加测试 app、测试路由或测试消息。

`frontend/e2e/support/test_server.py`：

- 导入 `create_app()`。
- 创建 `TestClock`、`SequenceTokenSource` 和 `RoomRegistry`。
- 注入生产 app。
- 在 Uvicorn 启动前添加测试专用 router。
- 只绑定 `127.0.0.1`。

测试推进：

```http
POST /__test__/rooms/{room_code}/advance
X-Test-Control: <parent-process-random-token>
```

请求体只允许 `seconds`。处理器推进 `TestClock`，再调用
`RoomActor.enqueue_timer_tick()`，使超时经过真实单写者队列和
`GameCore.tick()`，不绕过服务端。

### 7.9 现有 public_timeline 的填充

`PublicView.public_timeline` 已存在，但 S1 没有写入点。S3-P0 必须在
`apply_command()` 的公开事件收口处填充它：

- 只把 `PublicVisibility` 事件转换为 `PublicTimelineItem`。
- statement 使用确定性模板，不从玩家文本推断隐藏事实。
- `Phase -> 中文标签` 使用固定且穷尽的映射；statement 不得出现
  `NIGHT_WOLF`、`DAY_VOTE` 等原始枚举值。
- `PHASE_CHANGED`、死亡、放逐、无放逐、平票、超时、暂停、恢复和
  `GAME_ENDED` 均有稳定中文文本。
- `GAME_ENDED` statement 包含最终 winner 文案，但只包含公开胜负，不包含
  角色真值或夜间原因。
- statement 最大 240 字符，超长时使用模板本身的简短形式。
- 每条 item 的 `event_id`、`revision` 与原始公开事件一致。
- `public_timeline` 不进入 `state_hash()`；事件日志和回放语义不变。

Stage 的终局/时间线读取 `PublicView.public_timeline`。玩家终局页可额外通过
seat replay 获取公开 `GAME_ENDED` 事件。

## 8. 前端实时状态

### 8.1 协议模型

```ts
export type RoomUpdate =
  | SessionReadyMessage
  | PublicViewMessage
  | SeatViewMessage
  | HostControlMessage;

export interface SessionReadyMessage {
  type: "session.ready";
  server_time: string;
  snapshot: RoomSnapshot;
}

export interface PublicViewMessage {
  type: "public.view.updated";
  server_time: string;
  outbox_seq: number;
  public_view: PublicView;
}

export interface SeatViewMessage {
  type: "seat.view.updated";
  server_time: string;
  outbox_seq: number;
  seat_id: number;
  seat_view: SeatView;
}

export interface HostControlMessage {
  type: "host.control.updated";
  server_time: string;
  outbox_seq: number;
  host_control: HostControlView;
}
```

### 8.2 状态归并

`useRoomSession` 只维护：

- connection state。
- latest snapshot 或 update。
- last `outbox_seq`。
- last command ack map。
- UI-level pending command id。

它不计算 legal actions、票型、角色真值或下一阶段。所有规则事实来自服务端。

### 8.3 重连

连接状态机：

```text
idle -> connecting -> awaiting_auth -> ready
                   \-> retry_wait -> connecting
ready -> replaced / expired / closed
```

规则：

- 新连接只由 session/realtime 层拥有，不在 React render 中创建。
- open 后 5 秒内发送 auth。
- 4001：清除对应会话，不自动重试。
- 4003：停止重连并要求用户确认设备接管。
- 1001：立即重新认证。
- 1008 或 1013：指数退避，基值 500 ms，上限 8 s。
- 重连成功必须用新的 `session.ready` 替换旧快照。

## 9. 计时设计

`ui/Timer.tsx` 接收：

- `deadlineAt`。
- `serverTime`。
- `paused`。
- `pausedAt`。

收到更新时：

```ts
const remaining = deadlineAt === null
  ? 0
  : Math.max(0, Date.parse(deadlineAt) - Date.parse(serverTime));

const anchorMonotonic = performance.now();
const localDeadline = anchorMonotonic + remaining;
```

规则：

- 本地显示只使用 `performance.now()`。
- 同一 revision、phase 和 deadline 下，偏移小于 250 ms 不重锚。
- 暂停时冻结显示并显示暂停状态。
- 恢复后使用服务端新 deadline。
- 本地到 0 只改变提示，不发送超时命令。

## 10. 信息隔离与夜间遮罩

`SeatShell` 默认只挂载公共内容。

私密 reveal 组件：

- 初始不挂载角色、private facts 或 legal target 文本。
- 用户执行按住或滑动揭示后，私密节点才挂载。
- 松手、取消、失焦、页面隐藏或组件卸载时立即回盖并卸载。
- reveal 不持久化，刷新后默认关闭。
- 狼人互相可见的信息仍只来自自己的 `SeatView.private_facts`。

`StageShell`：

- 只接受 PublicView。
- TypeScript 层不存在 seat 或 host token 字段。
- 屏幕没有 command、pause、audit 或 correction 控件。

## 11. 样式与主题

`src/ui/tokens.ts` 只导出语义 token 名称和类型：

```ts
export const token = {
  surface: "var(--ww-surface)",
  surfaceRaised: "var(--ww-surface-raised)",
  text: "var(--ww-text)",
  textMuted: "var(--ww-text-muted)",
  action: "var(--ww-action)",
  danger: "var(--ww-danger)",
  success: "var(--ww-success)",
  phaseNight: "var(--ww-phase-night)",
  phaseDay: "var(--ww-phase-day)",
} as const;
```

`tokens.css` 保存实际值，并在 Tailwind 4 `@theme` 中映射。

游戏阶段：

```css
@custom-variant night (
  &:where([data-phase="night"], [data-phase="night"] *)
);
@custom-variant day (
  &:where([data-phase="day"], [data-phase="day"] *)
);
```

应用壳根据 `PublicView.phase` 设置 `data-phase="day|night"`。
`@theme` 必须映射 `--color-phase-night` 和 `--color-phase-day`，并在真实组件
中至少各使用一次 `night:` / `day:`。E2E 通过 computed style 断言阶段换色。

禁止：

- 使用 `dark:` 表示游戏昼夜。
- 在组件内写 `bg-slate-900`、`text-gray-500` 等原始色阶。
- 依赖 hover 才有主要操作。
- 使用 viewport 宽度缩放字体。

## 12. 可访问性

- 320 CSS px 宽度下无水平滚动。
- 触控目标最小 44x44 CSS px。
- 所有 icon-only 按钮有 `aria-label` 和 `title`。
- 倒计时用文本和视觉双重表达。
- 连接、暂停、重连、设备接管和房间关闭使用 `role="status"` 或
  `role="alert"` 宣布。
- dialog 打开后焦点进入，关闭后回到触发按钮。
- 角色卡和夜间动作支持键盘等价操作。

## 13. 错误处理

前端维护 `ErrorCode` 到简体中文文案的映射。未知 code 使用通用文案，不显示原始
服务端错误。

| 场景 | 用户动作 |
|---|---|
| `ROOM_NOT_FOUND` | 返回加入页 |
| `ROOM_FULL` | 提示房间已满 |
| `TOKEN_INVALID` / `TOKEN_EXPIRED` | 手机玩家重新加入；共享屏重新配对 |
| `RATE_LIMITED` | 显示倒计时后允许重试 |
| `REVISION_CONFLICT` | 等待当前快照后由用户重新操作 |
| `ILLEGAL_PHASE` | 刷新当前视图并显示阶段已变化 |
| `GAME_ENDED` | 进入终局或回放 |
| 未知错误 | 通用错误，不展示异常文本 |

## 14. 后端 S3-P0 文件边界

| 文件 | 变更 |
|---|---|
| `backend/src/werewolf_dm/domain/contracts.py` | `display` actor 形状 |
| `backend/src/werewolf_dm/domain/state_machine.py` | 公开事件填充 `public_timeline` |
| `backend/src/werewolf_dm/domain/visibility.py` | `paused`、活动票聚合字段 |
| `backend/src/werewolf_dm/application/rooms.py` | actor 类型、display token、配对、撤销、`server_time`、投票聚合、display 广播 |
| `backend/src/werewolf_dm/interfaces/http_ws/models.py` | 配对与 display REST DTO、WS `server_time` |
| `backend/src/werewolf_dm/interfaces/http_ws/authorization.py` | 新增共享 Bearer room/scope 授权 helper |
| `backend/src/werewolf_dm/interfaces/http_ws/display.py` | 新增配对、交换、撤销路由 |
| `backend/src/werewolf_dm/interfaces/http_ws/ws.py` | display 认证、消息拒绝、token 与 session 复检 |
| `backend/src/werewolf_dm/interfaces/http_ws/runtime.py` | display 默认频道 |
| `backend/src/werewolf_dm/interfaces/http_ws/app.py` | 挂载 display router |
| `backend/tests/unit/test_display_sessions.py` | display、token 和 pairing 单元测试 |
| `backend/tests/integration/http_ws/test_display_pairing.py` | REST 与 WS display 集成测试 |
| `backend/tests/integration/http_ws/test_public_state_additions.py` | `paused`、`server_time` 和投票进度回归 |
| `backend/tests/unit/test_s2_final_acceptance_repair.py` | 更新受 `server_time` 影响的断言 |
| `backend/tests/integration/http_ws/test_ws_*.py` | 更新严格消息形状断言 |
| `backend/tests/integration/http_ws/test_six_client_flow.py` | 扩展为 6 seat + host + display |
| `backend/tests/clocks.py` | S3 新测试共享的可推进时钟 helper |

不得修改 S1 golden replay 的语义。若 `server_time` 或 `paused` 改变现有 golden
结构，必须先停止并确认兼容策略。

## 15. 前端文件边界

S3-01 才能创建 `frontend/`。每个任务只能修改自己声明的 feature 和测试文件。

共享文件：

- `src/protocol/models.ts`：所有任务只增量追加，不重复定义消息。
- `src/realtime/RoomSocket.ts`：唯一连接实现。
- `src/session/storage.ts`：唯一 token 存储实现。
- `src/ui/tokens.ts` 和 `tokens.css`：唯一视觉 token 入口。

共享文件在同一任务内只允许一个 writer。需要并行工作时，由主线先冻结接口，再
分配 consumer 任务。

## 16. 测试策略

### 16.1 后端

- 单元测试覆盖 token 生命周期、配对攻击、actor 授权和投影字段。
- 集成测试覆盖 REST 交换、WebSocket 认证、消息拒绝和连接替换。
- 全量 S2 回归不得下降。
- `server_time` 使用固定或可推进测试时钟精确断言。

### 16.2 前端单元与组件

- `RoomSocket` 用 fake WebSocket 测认证、重连、close code 和清理。
- storage 用 fake Storage 测 schema、过期、损坏和 quota。
- Timer 用 fake performance clock 测重复锚定、暂停和恢复。
- player reveal 测未揭示不挂载、失焦回盖和键盘等价。
- host correction notice 测禁用说明。

### 16.3 E2E

Playwright 使用一个 Chromium browser、`workers=1` 和多个独立 context：

- 1 个 player context：320x568，其余 5 个为 375x667。
- 1 个 host context：390x844。
- 1 个 stage context：1920x1080。

服务器由 `frontend/e2e/support/test_server.py` 启动，时间为冻结值。测试通过
控制接口推进 timer，不执行 `sleep(30)`。
320 px context 必须断言 `documentElement.scrollWidth <= innerWidth`，并逐项
检查可见 button 的边界不小于 44x44 CSS px。

E2E 顺序：

1. host 创建房间。
2. 6 名玩家加入并提交 `JOIN_ROOM`。
3. 全部准备并确认角色。
4. 夜间行动完成后推进时钟。
5. 白天讨论、普通投票或 PK。
6. host 暂停和恢复。
7. 达到 `GAME_END`。
8. stage 全流程无 secret sentinel。
9. seat 刷新恢复。
10. replay 与状态一致。

## 17. 安全与信息纪律

- display token 不是 host token 的短权限副本；服务端不向 display 发送 host
  messages。
- pairing code 只证明短期投屏权，不证明任何游戏命令权。
- token、pairing code、token digest 不得进入日志、错误、URL 或 analytics。
- display audit 或 replay 请求固定返回 `ACTOR_NOT_AUTHORIZED`。
- 前端不得依赖属性隐藏保护秘密；服务端 projection 是唯一信任边界。
- 测试控制路由必须只绑定 loopback，并由父进程随机 token 保护。

## 18. 兼容与迁移

### 18.1 后端

- seat 和 host token 继续有效。
- `session.ready` 和 view updates 新增 `server_time`，属于加法变更。
- `PublicView` 新增 `paused`，`VoteSummary` 新增两个计数，属于加法变更。
- display 仅在 S3-P0 后可用。
- 房间关闭、token 过期和旧连接替换语义保持不变。

### 18.2 前端

- 新客户端只支持带 `server_time` 的 S3-P0 后端契约。
- 不实现 legacy S2 消息降级。
- 版本不匹配时显示重新加载或升级提示。

## 19. S3-P0 完成定义

S3-P0 只有同时满足以下条件才算完成：

- display actor 的 token、配对、撤销、订阅和消息拒绝有测试。
- `PublicView.paused`、`server_time` 和投票聚合计数有测试。
- 现有 `public_timeline` 有确定性填充，公开 `GAME_ENDED` 结果可被消费。
- 生产 app 不存在测试控制面。
- 原有 S2 full suite 至少保持 `395 passed, 6 skipped`。
- latency marker 至少保持 `4 passed, 2 skipped`。
- ruff、format 和 strict mypy 通过。
- `verify-delivery.ps1` 通过。
- 独立只读复核无 CRITICAL/HIGH/MEDIUM。

## 20. S3 完成定义

- player、stage、host 和 replay 路由均可恢复会话。
- 6 名玩家浏览器端到端完成完整对局。
- stage 全流程不出现任何 seat 或 host 私有载荷与控制。
- 夜间私密内容默认不挂载，揭示生命周期可测。
- 320 CSS px 和 1920x1080 均通过截图与布局检查。
- 主持人看到明确的纠错未开放说明。
- 前端 lint、typecheck、unit、build 和 E2E 全部进入 CI。
- 独立只读复核无阻塞项。

## 21. 风险与缓解

| 风险 | 缓解 |
|---|---|
| display actor 改动范围大 | S3-P0 独立任务；逐层测试；旧 seat/host 兼容 |
| 手机时钟漂移 | transport `server_time` 加 monotonic anchor |
| 配对码被暴力猜测 | 房间绑定、5 次失败失效、按来源限速、短期 TTL |
| Playwright 8 context OOM | 单 browser、单 worker、按 flow 拆分 |
| 测试控制面泄漏 | 独立测试 app、loopback、父进程随机 token |
| Tailwind token 漂移 | tokens.css 单一变量源、禁止原色 class |
| 夜间隐私被 DOM 检查发现 | 默认不挂载，reveal 结束卸载 |
| host recovery 假入口 | 显式禁用说明并记录未来 `S3-R` |

## 22. Changelog

### v1.1.0 - 2026-09-26

- 增加 `PublicView.paused_at`，修复共享屏暂停期间刷新后倒计时从错误
  剩余量恢复的问题。
- display pairing 响应增加 `expires_in_seconds`，消除客户端时钟偏差导致
  有效配对码被提前判废或失效码继续显示的问题。

### v1.0.0 - 2026-09-25

- 最终独立只读复核返回 `PASS`，无 CRITICAL/HIGH/MEDIUM；设计冻结。
- 闭合后续夜晚 `WITCH_SKIP`、首夜预言家非自身查验和白天投票目标选择器。
- 为 `public_timeline` 固化穷尽 Phase 中文映射，禁止输出原始枚举值。

### v0.1.0 - 2026-09-25

- 固化 S3 单 SPA、角色路由、协议层和 session 边界。
- 设计 display actor、display token、配对、交换、轮换和撤销。
- 设计 `PublicView.paused`、transport `server_time`、活动投票聚合进度。
- 设计测试组装服务器和 Playwright 六客户端 E2E。
- 固化夜间遮罩、Tailwind token 和可访问性约束。
