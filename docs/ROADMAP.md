# 路线图

## 已完成

- [x] S1 Headless 规则核心。
- [x] S2-01 FastAPI 骨架与健康检查。
- [x] S2-02 房间注册表、令牌与单写者 actor。
- [x] S2-03 REST 创建和加入房间。
- [x] S2-04 WebSocket 首帧认证。
- [x] S2-05 公共频道与公共视图更新。
- [x] S2-06 座位频道与跨座位拒绝。
- [x] S2-07 命令桥和 `GameCore.submit()` 集成。
- [x] S2-08 错误脱敏、安全错误映射和连接限速。
- [x] S2-09 真实时钟、暂停/恢复和 TimerScheduler。
- [x] S2-10 审计与回放导出。
- [x] S2-11 指标与延迟测量。
- [x] S2-12 六客户端端到端服务流程。
- [x] S2-13 交付门禁与独立复核。
- [x] S2 最终验收修复：token/房间生命周期、连接关闭、协议纪律、
  日志脱敏与 latency CI 门禁。
- [x] S3 React 前端：display/seat/host/replay 闭环、设计 token、
  六浏览器确定性 E2E，以及前端和 E2E CI/根交付门禁。
- [x] S4 Template-Only AI DM：严格模板契约、确定性 renderer、
  template-only service、RoomActor ordered outbox/transport、
  metrics/trace privacy、前端 `dm.message` 隔离与真实模板延迟回归。
- [x] S4-07 生产 `SEAT_PROMPT`：`RoomActor` 按当前 seat session 从夜阶段
  outbox 投递座位提示，离线槽不阻塞后续槽，浏览器证据使用真实生产消息；
  生产实现、真实浏览器 E2E、独立复核和交付门禁已完成；用户已于
  2026-09-28 正式验收通过。
- [x] P1 主持人恢复与最小持久化：SQLite 原子恢复事务、精确快照回退、
  force-template、host-only WS/UI、单一 `/audit`、跨 app 进程 restart
  recovery 和 seat/display/public privacy 负例已实现。2026-09-29 按用户
  指定的等价验收方式完成六座位真实协议、六人浏览器流程和重启恢复验收；
  该结论不替代真人主观体验判断。尚未 push、创建 PR、merge、tag 或
  release。

## 下一步

- [ ] 为已验收的 P1 分支取得明确的远端收口授权：push、创建 PR、通过
  required checks/CodeQL/隔离 latency gate 后 squash merge，并另行决定
  是否发布下一个 minor release。
- [ ] 安排合适的真人六人局域网体验复核，重点验证易用性、节奏和主持人
  主观负担；机制、协议、恢复与隐私已由代理等价验收覆盖。
- [ ] 后续版本化设计 provider/LLM 重入。

每项任务只有在实现、确定性测试、独立复核和用户验收都完成后才会标记为
完成。正式版本只会在对应阶段的验收出口通过后发布。当前本地 P1 验证
结果为 backend `773 passed, 10 skipped`、latency `8 passed, 2 skipped`、
前端 Vitest `175 passed`、Playwright `15 passed`，ruff、format、
strict mypy、lint、typecheck 和 build 均通过。根 `verify-delivery.ps1`
已在最终 handoff 更新前通过。S4 的历史本地与 CI 基线仍作为已验收参考。
CI 与根交付门禁同时覆盖后端、前端和隔离的 latency job。
