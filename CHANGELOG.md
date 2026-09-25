# Changelog

本项目从首个公开提交开始记录重要变更。格式参考 Keep a Changelog，
版本遵循 Semantic Versioning。

## Unreleased

### Added

- S1 Headless 规则核心、严格领域模型、投影、回放和确定性模拟。
- S2 FastAPI/WebSocket 实时接口骨架。
- 房间注册表、签名令牌、单写者 RoomActor 和限速连接。
- REST 房间创建/加入、WebSocket 首帧认证、公共/座位频道和命令桥。
- 严格错误白名单、全局异常处理器和 VIS-007 泄漏回归。
- GitHub Actions CI、CodeQL、Dependabot 和开源协作模板。
- Wheel 内置 Apache-2.0 许可文本和 PEP 561 `py.typed` 标记。
- 包版本与应用健康检查版本统一为 `0.2.0`。

### Security

- 未知异常仅记录在服务端，客户端只接收安全错误码和 `request_id`。
- token 原文、片段、摘要和隐藏角色/事实不会进入错误响应。
- 跨座位频道订阅和跨租户 actor 覆盖均被拒绝。
