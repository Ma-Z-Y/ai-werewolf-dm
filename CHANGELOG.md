# Changelog

本项目从首个公开提交开始记录重要变更。格式参考 Keep a Changelog，
版本遵循 Semantic Versioning。

## Unreleased

_暂无变更。_

## 0.3.0 - 2026-09-28

### Added

- S1 Headless 规则核心、严格领域模型、投影、回放和确定性模拟。
- S2 FastAPI/WebSocket 实时接口骨架。
- 房间注册表、签名令牌、单写者 RoomActor 和限速连接。
- REST 房间创建/加入、WebSocket 首帧认证、公共/座位频道和命令桥。
- 真实 UTC 时钟、FastAPI lifespan 生产 registry，以及暂停、恢复和竞态
  安全的 `TimerScheduler`。
- lifespan 只清理本生命周期拥有的 registry，并在关闭后重置，支持同一 app
  的可重复生命周期；外部注入 registry 保持不动。
- 严格错误白名单、全局异常处理器和 VIS-007 泄漏回归。
- GitHub Actions CI、CodeQL、Dependabot 和开源协作模板。
- Wheel 内置 Apache-2.0 许可文本和 PEP 561 `py.typed` 标记。
- 包版本与应用健康检查版本统一为 `0.2.0`。
- S2-10 至 S2-13：审计/回放导出、指标与延迟测量、真实六客户端端到端流程、
  delivery gate、nearest-rank p95 和 latency marker CI 门禁。
- S2 最终验收修复：过期 token/房间关闭、same-actor 重连替换、满队列
  2 秒定时关闭、生产 reaper 生命周期、房间/连接上限、严格协议活动门和
  真实 `RoomActor` outbox LAT-005 证据。
- S3 React 前端：手机玩家、共享只读舞台、主持人控制台、终局与个人
  回放，以及隐私、重连、计时和命令 ACK/冲突处理。
- Playwright 六人确定性浏览器闭环，覆盖创建、配对、加入、角色、夜间、
  讨论、投票、PK、暂停/恢复、重连、终局和回放。
- GitHub Actions 前端与浏览器 E2E jobs；根交付门禁现在运行前端
  lint、类型检查、单元测试、构建和 E2E，并检查前端源码状态新鲜度。
- S3 验收修复：20 秒 WebSocket 心跳、4003 接管终态、per-tab 会话抑制、
  暂停刷新计时、SET_READY 重试、host 在途副作用隔离、服务端相对配对
  TTL、回放私密事实二次过滤和前端/TypeScript CodeQL。
- S4 Template-Only AI DM：严格模板契约、fact allowlist、确定性
  renderer、同步 template service、RoomActor ordered outbox 与
  DM transport sequence、严格 metrics/trace privacy。
- S4 前端 `dm.message`：公开模板只进入共享舞台，seat 模板只进入匹配
  座位；消息渲染不暴露 trace、token、session、provider、prompt 或
  private facts。
- S4 E2E 与延迟：固定 room/seed/session/catalog 字节确定性、
  六客户端同 revision 证据、暂停/重连/终局顺序，以及真实模板
  LAT-001/004/005 和独立 latency CI job。
- S4-07 生产 `SEAT_PROMPT`：夜阶段为存活狼人、预言家和女巫生成座位槽，
  仅向当前 seat session 投递；离线槽安全完成且不阻塞后续槽，同时删除
  E2E-only seat-message injection。
- S4-07 admission hardening：显式 intent 必须严格重校验并匹配 outbox
  audience；缺失 item、非法 intent 和 audience mismatch 都在任何状态
  变更前 fail closed。

### Security

- 未知异常与 reaper 异常仅记录 exception type，不记录异常文本或
  traceback；客户端只接收安全错误码和 `request_id`。
- token 原文、片段、摘要和隐藏角色/事实不会进入错误响应。
- 跨座位频道订阅和跨租户 actor 覆盖均被拒绝。
- 前端传递依赖 `ansi-regex` 通过 pnpm override 固定到已修复的 `5.0.1`，
  消除 Dependabot 的安全更新失败。
- 房间删除或过期会主动关闭已认证连接；malformed ping 和未知协议消息不会
  刷新空闲时间或绕过连接洪泛控制。
- S4 DM 渲染继续执行 public/seat 分区；浏览器 E2E 阻断 provider 与非
  loopback 外联。生产 `SEAT_PROMPT` 已由 `RoomActor` 从领域 outbox
  生成，浏览器证据直接消费真实 seat 消息；provider/LLM 仍未启用。

## 0.2.0

### Changed

- 包版本与应用健康检查版本统一为 `0.2.0`。
