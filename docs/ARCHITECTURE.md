# 架构入口

本文件是面向贡献者的导航页。冻结的产品和规则契约位于
[`specs/README.md`](specs/README.md)。

## 分层

```mermaid
flowchart LR
    Client[玩家手机 / 共享屏] --> Transport[FastAPI + WebSocket]
    Transport --> Room[RoomRegistry / RoomActor]
    Room --> Core[GameCore]
    Core --> Domain[apply_command + Finite State Machine]
    Domain --> Views[PublicView / SeatView / Audit]
    Views --> Room
    Room --> Transport
```

### `domain/`

纯领域层，不依赖传输、数据库、系统时间或 LLM。

- `model.py`：严格且不可变的游戏状态模型。
- `contracts.py`：命令、事件、投影和错误码契约。
- `state_machine.py`：唯一状态变化入口 `apply_command()`。
- `visibility.py`：公共、座位和主持人视角的授权投影。
- `replay.py`：确定性状态摘要。

### `application/`

运行时编排层。

- `core.py`：`GameCore.submit()`、`tick()`、revision 与幂等命令。
- `replay.py`：命令和系统超时回放。
- `simulation.py`：无头策略、种子局、不变量和 golden replay。
- `rooms.py`：房间、令牌、单写者 actor 与订阅分发。
- `host_recovery.py`：暂停前置校验、六类主持人补丁、精确快照回退、
  force-template 和恢复审计事务。

### `infrastructure/`

- `persistence.py`：SQLite 房间、令牌摘要、事件、命令去重、运行时、
  DM trace、recovery audit 和快照的原子读写边界。

### `interfaces/http_ws/`

传输适配层。

- REST 创建和加入房间。
- WebSocket 首帧认证、频道订阅和命令桥。
- 严格错误白名单、请求限速、连接背压和脱敏。

## 关键边界

- `domain/` 不得导入 FastAPI、WebSocket、数据库或 LLM SDK。
- 所有游戏状态变化必须经过 `apply_command()`。
- 客户端 actor 必须来自令牌，不得由请求体覆盖。
- 公共、座位和主持人数据只能通过对应投影读取。
- 主持人恢复写入必须与事件、状态、runtime、audit 和快照处于同一事务。
- 公开、seat 和 display 载荷不得包含 snapshot、diff、raw events 或 token；
  唯一的活动审计导出入口是 host-only `/audit`。
- 未识别异常只写入服务端日志，客户端只收到安全错误码和 `request_id`。

## 重启恢复

生产 lifespan 从 `WEREWOLF_DM_DB_PATH` 打开 SQLite store，恢复房间、
令牌摘要、事件、命令去重、runtime、recovery audit 和快照后再启动
`RoomActor`。P1 Task 8 的浏览器回归会在同一测试 run 内关闭第一个 app
进程，用同一临时数据库启动第二个进程，并使用原有 host/seat/display
凭据重连；该测试路由和注入只存在于 `frontend/e2e/support/test_server.py`，
不会进入生产 app。

## 验证

后端同时包含单元测试、HTTP/WebSocket 集成测试、100 局种子模拟和 9 个
golden replay。CI 在 Python 3.12 上运行 pytest、ruff、格式检查和 strict
mypy。P1 恢复回归额外覆盖跨 app 进程重启、快照集合恢复无重复、host-only
audit、seat replay 拒绝 host-only payload，以及 seat/display 的隐私负例。
