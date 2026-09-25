# Changelog

本项目从首个公开提交开始记录重要变更。格式参考 Keep a Changelog，
版本遵循 Semantic Versioning。

## Unreleased

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

### Security

- 未知异常与 reaper 异常仅记录 exception type，不记录异常文本或
  traceback；客户端只接收安全错误码和 `request_id`。
- token 原文、片段、摘要和隐藏角色/事实不会进入错误响应。
- 跨座位频道订阅和跨租户 actor 覆盖均被拒绝。
- 房间删除或过期会主动关闭已认证连接；malformed ping 和未知协议消息不会
  刷新空闲时间或绕过连接洪泛控制。
