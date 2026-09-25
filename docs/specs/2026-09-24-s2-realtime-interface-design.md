---
spec_id: s2-realtime-interface
version: 1.2.0
status: frozen
frozen_at: 2026-09-24
owner: architecture
depends_on:
  - product-constitution@1.1.0
  - rulepack-v1@1.1.0
  - system-design@1.1.0
  - verification-matrix@1.1.0
  - s1-headless-game-core@v6
---

# S2 实时接口层设计 v1.0

## 1. 目标

把已验收的 S1 `GameCore` 包装成一个可被多客户端实时连接的单进程游戏服务：

- 创建房间和加入房间。
- 主持人令牌和座位令牌。
- WebSocket 首帧认证。
- 公共视图和座位视图分区推送。
- 玩家命令只经 `GameCore.submit()`。
- 真实计时器驱动 `GameCore.tick()`。
- 断线重连返回最新完整视图。
- HTTP 和 WebSocket 错误脱敏。
- 玩家回放和主持人审计导出权限隔离。
- 观测广播延迟和慢连接。

S2 不改变 S1 规则、状态机、事件语义或投影语义。

## 2. 为什么先做 S2

- S1 已经提供可复用 `GameCore`，但没有网络、房间、令牌和推送。
- AI DM 依赖真实事件流、房间生命周期和视图投影。
- S2 是纯基础设施，可先验证多人连接、授权、顺序和恢复。
- 前端 UI 属于 S3；S2 只交付服务端协议和可测试适配层。

## 3. 已冻结决策

1. 使用 Python 3.12、FastAPI、Starlette WebSocket 和 Pydantic v2。
2. `domain/` 和现有 `application/core.py` 不修改。
3. 新增房间编排放在 `application/rooms.py`，不得导入 FastAPI 或 WebSocket 类型。
4. `RealClock` 只在 `interfaces/http_ws/runtime.py` 中实例化。
5. 测试继续使用 `FrozenClock` 或可注入 fake clock，禁止 `sleep()`。
6. 每个房间只有一个 `RoomActor` 串行处理命令、超时和广播候选。
7. `GameCore.submit()` 和 `GameCore.tick()` 仍是唯一状态变化入口。
8. 每个 WebSocket 连接有独立有界发送队列和唯一 writer task。
9. WebSocket 使用首帧令牌认证，不使用 URL query token。
10. 主持人令牌和座位令牌分离，均按 SHA-256 摘要保存在内存。
11. S2 只提供完整视图快照重连，不做事件 gap replay。
12. S2 无 SQLite、Redis、Nakama、Colyseus、广播中间件或水平扩展。
13. 进程重启后房间丢失；这是明确限制，不声称持久恢复。
14. `CommandResult.event_ids` 不进入网络响应。
15. `LAT-002` 和 `LAT-003` 的真实 LLM 复杂播报测试延后到 S4。

## 4. 模块边界

```text
backend/src/werewolf_dm/
  application/
    rooms.py                  # RoomActor、RoomRegistry、令牌服务、订阅抽象
  interfaces/
    __init__.py
    http_ws/
      __init__.py
      app.py                  # FastAPI app factory、健康检查、依赖装配
      models.py               # REST 与 WebSocket 严格 Pydantic DTO
      rooms.py                # REST 房间、加入和快照接口
      auth.py                 # HTTP Authorization 与 WS 首帧令牌适配
      ws.py                   # WebSocket 连接、认证、订阅、命令、心跳
      runtime.py              # RealClock、TimerScheduler、连接和 writer 生命周期
      errors.py               # 白名单错误适配层
      audit.py                # 玩家回放和主持人审计导出
      metrics.py              # 内存计数器与延迟直方图
```

新增依赖只允许：

```text
fastapi
uvicorn[standard]
httpx
websockets
pytest-asyncio
```

## 5. 运行时模型

### 5.1 RoomActor

`RoomActor` 是每个房间的唯一写者，内部拥有：

```python
class RoomActor:
    room_id: UUID
    room_code: str
    core: GameCore
    events: asyncio.Queue[RoomEvent]
    subscribers: dict[SubscriptionId, RoomSubscriber]
    token_service: TokenService
    outbox_seq: int
    closed: bool
```

必须满足：

- 所有 `GameCore.submit()` 调用来自 `RoomActor` 的事件循环。
- 所有 `GameCore.tick()` 调用也来自 `RoomActor`。
- 连接处理协程只把事件放入房间队列，不直接读改 `GameCore`。
- 同一房间事件严格先进先出。
- RoomActor 不知道 FastAPI、WebSocket 或连接 socket。
- `RoomEvent` 至少包含 `SubmitCommandEvent`、`TimerTickEvent`、`AttachSubscriberEvent`、`DetachSubscriberEvent` 和 `StopEvent`。
- `start()` 创建唯一 `run()` task；`stop()` 投递停止事件、拒绝未完成 Future、关闭订阅并等待 task 退出。
- `submit_command()`、`timer_tick()`、`attach_subscriber()`、`detach_subscriber()` 和快照请求都只通过同一队列执行。
- 每个成功 submit/tick 后，RoomActor 在同一串行回合内完成状态提交、`outbox_seq` 分配、授权投影和订阅者封送。
- RoomActor 同时拥有 TimerScheduler task；`start()` 启动两者，`stop()` 停止两者。
- `stop()` 取消 `timer_task` 后必须在 `contextlib.suppress(asyncio.CancelledError)` 下 await，再 await `RoomActor` task，避免待销毁协程警告。

### 5.2 RoomRegistry

`RoomRegistry` 保存 `room_code -> RoomActor`：

- `create_room()` 生成内部 UUID、短房间码、主持人令牌和未启动的 RoomActor；调用方必须再调用 `start_room()`。
- `start_room()` 启动 RoomActor 与其 TimerScheduler 生命周期。启动前提交命令、订阅或时钟事件返回 `ROOM_NOT_STARTED`，不得静默排队。
- `max_rooms` 默认 256；达到上限时创建房间返回 `ROOM_LIMIT_REACHED`。
- `join_room()` 分配下一个空座位并签发座位令牌。
- `get_by_code()` 只返回 RoomActor，不暴露内部异常。
- S2-02 一次性冻结以下 Registry API，后续任务只允许使用，不得再自行扩张：
  - `get_by_code(room_code) -> RoomActor`
  - `rooms_by_id() -> dict[UUID, RoomActor]`
  - `snapshot(actor, record) -> RoomSnapshot`
  - `actor_for(record) -> AuthenticatedActor`
- `remove_room()` 只由 TTL、终局后的闲置清理或测试关闭调用，并停止 RoomActor 与其 TimerScheduler。
- 房间码全局唯一；碰撞时重试，超过上限返回内部错误。
- `reap_expired()` 按 `expires_at` 和 `last_activity_at` 清理房间。
- 清理房间时同步删除该房间全部令牌。
- 全局连接数默认上限为 1024；达到上限时新 WebSocket 以 `1013` 关闭且不进入 RoomActor。

### 5.3 订阅

`RoomSubscriber` 是 RoomActor 与传输层的接口：

```python
class RoomSubscriber(Protocol):
    subscription_id: UUID
    actor_type: Literal["seat", "host"]
    seat_id: int | None
    def offer(self, message: ServerMessage) -> bool:
        raise NotImplementedError
    def request_close(self, code: int) -> None:
        raise NotImplementedError
```

`offer()` 必须非阻塞：

- 返回 `True` 表示已进入该连接的有界发送队列。
- 返回 `False` 表示连接落后。
- RoomActor 不等待 socket 发送。
- `request_close()` 由 RoomActor 在重连替换等场景同步调用，传输层负责异步清理发送队列并关闭 socket。

## 6. 令牌与认证

### 6.1 令牌模型

- `HostToken`：房间级主持人权限。
- `SeatToken`：绑定一个座位，座位号为 1-6。
- 令牌是不透明 256-bit 随机值，以 URL-safe Base64 返回。
- 服务端不保存明文令牌，只保存 `sha256(token)`。
- 令牌记录包含 `room_id`、`actor_type`、`seat_id`、`issued_at`、`expires_at`。
- 默认有效期为 4 小时；房间默认 TTL 为 6 小时。

令牌生成通过协议注入：

```python
class TokenSource(Protocol):
    def token(self) -> str:
        raise NotImplementedError

    def room_code(self) -> str:
        raise NotImplementedError
```

- 生产使用 `secrets`。
- 测试使用固定序列 source，保证令牌和房间码可预测。
- `TokenService` 按 `room_id` 维护独立座位占用表，不跨房间共享。
- 只有未过期且属于当前房间的座位令牌占用座位；过期座位可以重新分配。
- 删除房间时级联删除主持人令牌和全部座位令牌。
- 已认证连接在每个接收回合和每个消息处理前复检 `expires_at`；令牌过期后立即以 `4001` 关闭。

### 6.2 WebSocket 首帧认证

1. 服务端接受连接。
2. 服务端发送 `auth.required`。
3. 客户端必须在 5 秒内发送 `auth`：

```json
{
  "type": "auth",
  "token": "<opaque-token>",
  "last_seq": 0
}
```

4. 服务端验证摘要、过期时间、房间和权限。
5. 成功后按令牌绑定默认频道：座位令牌为 `public + seat`，主持人令牌为 `public + host.control`。
6. 发送 `session.ready`，唯一顶层形状为：

```json
{
  "type": "session.ready",
  "snapshot": {
    "room_id": "uuid",
    "room_code": "ROOM01",
    "revision": 0,
    "outbox_seq": 0,
    "public_view": {},
    "seat_view": {},
    "host_control": null
  }
}
```

7. 认证失败使用 `4001` 关闭；认证超时使用 `4002` 关闭；响应不返回令牌内容。

不把令牌放入 URL、日志、异常或回显。

## 7. REST 合同

所有响应使用严格 Pydantic 模型。

### 7.1 健康检查

```http
GET /healthz
```

返回：

```json
{
  "status": "ok",
  "version": "0.2.0",
  "active_rooms": 0
}
```

### 7.2 创建房间

```http
POST /rooms
```

请求：

```json
{
  "display_name": "房主"
}
```

响应：

```json
{
  "room_id": "uuid",
  "room_code": "AB12CD",
  "host_token": "opaque-once",
  "expires_at": "datetime"
}
```

### 7.3 加入房间

```http
POST /rooms/{room_code}/join
```

请求：

```json
{
  "display_name": "玩家"
}
```

响应：

```json
{
  "room_id": "uuid",
  "seat_id": 1,
  "seat_token": "opaque-once",
  "expires_at": "datetime"
}
```

房间满时返回 `ROOM_FULL`，房间不存在返回 `ROOM_NOT_FOUND`。
`join` 只预留座位并签发令牌；玩家必须在 WebSocket 中发送一次 `JOIN_ROOM` 命令，提供 display name，才能写入 `GameCore` 玩家集合。

### 7.4 玩家回放

```http
GET /rooms/{room_code}/replay
Authorization: Bearer <seat-token>
```

调用 S1 `project_player_replay()`，只返回公共事件和该玩家私密事实。
令牌记录解析后必须核对 `room_id` 与 URL 中 `room_code` 对应房间一致。

### 7.5 主持人审计

```http
GET /rooms/{room_code}/audit
Authorization: Bearer <host-token>
```

调用 S1 `project_host_audit()`。
主持人令牌不能用于玩家回放，座位令牌不能用于主持人审计。

## 8. WebSocket 合同

### 8.1 客户端消息

判别联合：

- `auth`
- `command`
- `subscribe`
- `ping`

`command` 只携带 `CommandEnvelope`，`actor` 从已认证连接上下文生成，不从客户端接收。

`subscribe` 只允许请求：

- `public`
- 与认证令牌座位一致的 `seat`
- 主持人认证后的 `host.control`

任何跨座位或跨房间订阅返回 `CHANNEL_FORBIDDEN`。
订阅成功后，将频道加入连接 sink，并由 RoomActor 立即发布该频道的当前视图；它不会再次发送 `session.ready`。

### 8.2 服务端消息

- `auth.required`
- `session.ready`
- `public.view.updated`
- `seat.view.updated`
- `host.control.updated`
- `command.ack`
- `room.paused`
- `room.resumed`
- `game.ended`
- `pong`
- `error`

每条房间消息带单调递增 `outbox_seq`。

### 8.3 命令确认

网络版 `command.ack` 只包含：

```json
{
  "type": "command.ack",
  "command_id": "uuid",
  "accepted": true,
  "revision": 4,
  "error_code": null,
  "outbox_seq": 12
}
```

禁止返回 `event_ids`、原始事件、异常字符串、traceback 或隐藏事实。

## 9. 连接、广播与背压

### 9.1 每连接 writer

- 每连接一个 `asyncio.Queue(maxsize=64)`。
- 每连接一个 writer task。
- 只有 writer task 调用 `websocket.send_json()`。
- RoomActor 和连接处理器使用 `offer()`/`put_nowait()`。
- 队列满时计数；连续 3 次满或超过 2 秒未恢复，以 `1013` 关闭。
- 关闭后清除连接注册和订阅，不删除座位令牌。
- `interfaces/http_ws/runtime.py` 提供 `ConnectionSink`，`ws.py` 不直接发送房间广播。
- 同一 `(actor_type, seat_id)` 重连时，RoomActor 在同一队列回合内移除旧 subscriber、调用旧连接的 `request_close(4003)`，再安装新 subscriber。

### 9.2 有序 outbox

- RoomActor 为每条房间消息分配 `outbox_seq`。
- 同一连接的消息顺序等于 RoomActor 分配顺序。
- `session.ready` 的 `outbox_seq` 是当前基线。
- attach、快照和 register 在 RoomActor 的同一串行回合内完成；后续更新序号严格大于基线。
- 重连客户端忽略小于基线序号的旧消息。

### 9.3 心跳与空闲连接

- 客户端每 20 秒发送 `ping`。
- 服务端回 `pong`。
- 60 秒无任何客户端消息时，服务端主动关闭连接。
- 心跳不进入房间命令队列，不改变游戏状态。
- WebSocket 只允许一个 receive/message-pump loop；S2-04 的认证后循环通过 `asyncio.wait_for(..., timeout=60)` 同时承担空闲检测，S2-05 与 S2-07 在同一循环内增加 subscribe/command dispatch，不得再启动第二个 receiver。
- receive 超时取“会话空闲剩余时间”和“令牌剩余有效期”的较小值。
- 形状类似 `ping` 的消息即使 schema 不合法，也必须先消耗 `ping/auth` 控制令牌桶。只有通过严格协议校验并进入对应分支的消息才刷新空闲时间；畸形 `ping` 和未知/非法协议消息不得绕过空闲关闭或洪泛关闭。

## 10. 真实计时器

### 10.1 RealClock

```python
class RealClock:
    def __call__(self) -> datetime:
        return datetime.now(UTC)

    def set(self, now: datetime) -> None:
        del now
```

`RealClock` 只在 `interfaces/http_ws/runtime.py` 实例化，并通过现有 `Clock` 协议注入 `GameCore`。生产 `RoomRegistry` 由 `runtime.py` 的 `build_production_registry()` 创建，`app.py` 只在 lifespan 中调用它。

### 10.2 TimerScheduler

- RoomActor 暴露 `deadline_changed: asyncio.Event`。
- TimerScheduler 必须读取当前 deadline/pause 快照，调用 `clear()` 后再次复检；若复检发现状态已变化，立即 continue，避免 `clear()` 丢掉刚发生的信号。
- 每次成功 submit/tick 后更新事件并唤醒调度器。
- TimerScheduler 等待“deadline 变化或截止时间到期”，不轮询。
- 暂停时只等待 deadline 变化，不等待旧截止时间。
- 到期后投递带 `revision` 和 `deadline_at` 的 `TimerTickEvent`。
- RoomActor 忽略 revision 或 deadline 已变化的过期 tick。
- RoomActor 调用 `GameCore.tick()`，而不是直接应用 timeout。
- TimerScheduler 与连接 writer task 分离。
- `FrozenClock` 测试通过显式 `TimerTick` 驱动，不使用 `sleep()`。

暂停时 RoomActor 仍可接收宿主命令和重连，但不执行 `TimerTick`。

### 10.3 房间 TTL 与清理

- `RoomActor` 保存 `expires_at` 和 `last_activity_at`。
- join、成功 command、tick、attach 和 detach 刷新 `last_activity_at`。
- `RoomRegistry.reap_expired()` 删除过期或终局后长期闲置的房间。
- 删除房间时同步删除该房间令牌。
- 测试通过 `FrozenClock` 调用 `reap_expired()`，不使用后台 sleep。
- 生产 reaper 只由 `interfaces/http_ws/runtime.py` 启动。
- app-owned lifespan 创建受控 reaper task，默认每 60 秒调用 `reap_expired()`；shutdown 时取消并 await。注入的 registry 不由 lifespan 接管。
- `get_by_code()`、`join_room()` 和 `rooms_by_id()` 必须立即排除已过期房间，即使 reaper 尚未运行；WebSocket 认证只能解析这些未过期房间。

## 11. 重连与房间生命周期

重连步骤：

1. 客户端使用原座位令牌执行首帧认证。
2. 服务端验证令牌和房间仍在。
3. 若旧连接仍在，关闭旧连接并替换为新连接。
4. 发送最新 `session.ready`：
   - `public_view`
   - 对应座位 `seat_view`
   - 或主持人 `host.control`
   - 当前 `revision`
   - 当前 `outbox_seq`
5. 清理旧连接订阅。
6. 恢复后续推送。

不重放断线期间的全部事件。S2 的恢复单位是最新完整视图。

房间生命周期：

```text
created -> open -> playing -> ended -> idle
```

- `created/open/playing/ended` 都由 S1 房间和阶段决定。
- `idle` 由接口层维护，不写入领域状态。
- 房间在终局后或长期无活动时清理。
- S2 进程重启后房间消失。

## 12. 错误脱敏

公开错误码白名单：

- `BAD_REQUEST`
- `ROOM_NOT_FOUND`
- `ROOM_FULL`
- `ROOM_LIMIT_REACHED`
- `TOKEN_INVALID`
- `TOKEN_EXPIRED`
- `CHANNEL_FORBIDDEN`
- `ACTOR_NOT_AUTHORIZED`
- `ILLEGAL_PHASE`
- `INVALID_TARGET`
- `REVISION_CONFLICT`
- `PLAYER_DEAD`
- `NOT_CURRENT_SPEAKER`
- `VOTE_ROUND_CLOSED`
- `WOLF_CONSENSUS_PENDING`
- `POTION_ALREADY_USED`
- `WITCH_SELF_RESCUE_FORBIDDEN`
- `GAME_ENDED`
- `HOST_RECOVERY_NOT_IN_S1`
- `RATE_LIMITED`
- `CONNECTION_LAG`
- `INTERNAL_ERROR`

该列表必须包含全部 S1 `CommandErrorCode` 值。`CommandErrorCode` 是领域错误码，S2 `ErrorCode` 是网络错误码超集；两者必须保持包含关系，而不是重新定义。

错误响应：

```json
{
  "code": "INVALID_TARGET",
  "message": "目标不合法",
  "request_id": "uuid"
}
```

禁止：

- `str(exception)`
- traceback
- 原始 `event_id` 列表
- 隐藏角色事实
- 内部房间对象路径
- token 摘要或 token 片段

## 13. 最小限流

- 每连接命令令牌桶容量 20，补充速率每秒 5。
- `auth`、`ping` 使用独立小配额，防止冒充正常心跳的洪水。
- 超限返回 `RATE_LIMITED`。
- 连续超限次数达到阈值后关闭 WebSocket。
- S2 使用进程内限流；多实例限流延后。

## 14. 可观测性

至少记录并暴露：

- 活跃房间数。
- 活跃连接数。
- 认证失败数。
- 慢连接关闭数。
- 每连接发送队列深度。
- 命令处理延迟。
- 公共和座位广播延迟。
- `outbox_seq`。

`GET /metrics` 返回严格 Pydantic JSON：

```json
{
  "active_rooms": 1,
  "active_connections": 6,
  "auth_failures": 0,
  "slow_connection_closes": 0,
  "command_latency_ms_p95": 1.2,
  "broadcast_latency_ms_p95": 3.4,
  "max_connection_queue_depth": 0
}
```

## 15. 延迟范围

| ID | S2 处理 |
| --- | --- |
| `LAT-001` | 高频模板消息进入客户端队列 p95 小于 200ms |
| `LAT-002` | 真实 LLM 复杂播报延后到 S4；S2 只保留测量接缝 |
| `LAT-003` | LLM 1.5 秒降级延后到 S4 |
| `LAT-004` | 6 个 WebSocket 客户端广播 p95 小于 300ms |
| `LAT-005` | S2 模板/视图消息从 RoomActor outbox 分配到 writer queue 发送完成不超过 500ms；真实 LLM 回退仍属于 S4 |

延迟测试使用 `@pytest.mark.latency`，在普通单元测试中默认跳过。

## 16. 明确禁止

- 修改 S1 `domain/`、`application/core.py`、`application/replay.py` 或 golden。
- 在 `domain/` 或 `application/` 导入 FastAPI、Starlette、WebSocket、uvicorn 或 httpx。
- 让连接处理器直接调用 `apply_command()`。
- 直接从客户端接收 `AuthenticatedActor`。
- 广播完整 `GameState`、事件 ID、traceback 或原始错误。
- 使用 query token 或长期 token 作为唯一安全边界。
- 在测试中使用 `sleep()` 等待计时器。
- 引入 Redis、Nakama、Colyseus、数据库或前端。
- 把 `LAT-002/003` 包装成已经实现。

## 17. S2 验收出口

- `S2-01` 至 `S2-13` 全部计划任务完成并通过独立复核。
- 6 个模拟客户端完成至少一局无头服务端流程。
- 公共/座位频道和跨座位订阅测试通过。
- 命令、重连、暂停、恢复和导出测试通过。
- `VIS-006`、`VIS-007`、`VIS-008`、`VIS-010`、`VIS-012`、`VIS-014` 相关服务端测试通过。
- `LAT-001`、`LAT-004`、`LAT-005` 通过。
- `LAT-002`、`LAT-003` 明确标记 S4 延后。
- 无 token、异常或隐藏事实泄漏。
- delivery gate 通过。

## 18. Changelog

### v1.1.0 - 2026-09-24

- 将 S1 `CommandErrorCode` 的全部取值纳入 S2 `ErrorCode` 超集。
- 明确 `RoomActor.stop()` 必须先 cancel 并 await timer task。
- 冻结 `RoomRegistry` 的 `get_by_code`、`rooms_by_id`、`snapshot`、`actor_for` API。
- 明确 TimerScheduler 在 `clear()` 后复检，避免丢失刚发生的 deadline 变化。
- 明确 WebSocket 只有一个 receive/message-pump loop，并将 S2-04 的空闲检测边界写清。

### v1.2.0 - 2026-09-25

- 明确 `create_room()` 与 `start_room()` 分工，并禁止未启动房间静默排队。
- 增加房间数、连接数上限和 `ROOM_LIMIT_REACHED` 错误码。
- 已认证 WebSocket 在会话期间持续校验令牌过期时间。
- 同一 actor 重连时原子替换旧 subscriber，并以 `4003` 关闭旧连接。
- ConnectionSink 关闭流程显式处理 `OSError`、取消 writer、清空未完成队列并始终释放等待者。
- 畸形 ping 同样消耗控制令牌桶。
- 生产 lifespan 启动并回收周期房间 reaper，过期房间立即拒绝 lookup/join。
- `/metrics` 增加 `max_connection_queue_depth`。
- LAT-005 明确为 S2 outbox-to-writer 口径，不宣称实现 S4 LLM fallback。
