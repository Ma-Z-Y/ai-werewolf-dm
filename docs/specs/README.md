# 规格索引

当前冻结基线为验证矩阵 v1.5.6 / S4 design v1.1.6 / S4 constitution
v1.1：

S1、S2、S3 历史基线由 v1.3 提供；v1.4 新增 LLM 路径候选规格；v1.5
根据 provider 失败结果把当前 S4 收窄为 template-only，并将 provider
条款标记为 deferred；v1.5.1 增加模板键、unused fact、admission 和
确定性验收补强。v1.5.3 / v1.1.3 统一 DM wire transport 序列，并增加
投递失败 trace。v1.5.4 / v1.1.4 明确 offline seat slot 的 silent
ineligible completion 和显式 audience mismatch fail-closed。v1.5.5 /
v1.1.5 进一步规定显式 intent 的 outbox item 存在性与 audience
一致性校验先于 offline/duplicate/stale 短路，并增加 stale-session 负回归。
v1.5.6 / v1.1.6 进一步要求显式 intent 严格重校验，非法 channel 以
`ANNOUNCEMENT_INTENT_INVALID` 在任何状态变更前 fail closed。
查询修订为结构化 `TemplateVariantKey -> select()`，并把 SHA-256 digest
降为独立纯函数，只用于 trace、日志关联和跨实例校验。

1. [产品宪法](2026-09-23-product-constitution.md)
2. [RulePack v1](2026-09-23-rulepack-v1.md)
3. [系统设计](2026-09-23-system-design.md)
4. [验证矩阵 v1.5.6](2026-09-23-verification-matrix.md)
5. [S2 实时接口层设计 v1.2](2026-09-24-s2-realtime-interface-design.md)
6. [S3 前端产品宪法 v1.1](2026-09-25-s3-frontend-constitution.md)
7. [S3 前端与协议预检设计 v1.1](2026-09-25-s3-frontend-design.md)
8. [S4 AI DM 设计 v1.1.6](2026-09-26-s4-ai-dm-design.md)
9. [S4 AI DM 产品宪法 v1.1](2026-09-26-s4-ai-dm-constitution.md)

## S4 Current

- [验证矩阵 v1.5.6](2026-09-23-verification-matrix.md)
  - 状态：`frozen`
  - 当前 S4 只验收模板路由、渲染、隐私、outbox、恢复和延迟；
    provider/LLM 行标记为 deferred。
- [S4 AI DM 设计 v1.1.6](2026-09-26-s4-ai-dm-design.md)
  - 状态：`frozen`
  - 描述 template-only registry、renderer、service、metrics、有序 outbox
    和未来 provider 重入附录。
- [S4 AI DM 产品宪法 v1.1](2026-09-26-s4-ai-dm-constitution.md)
  - 状态：`frozen`
  - 将 D1 至 D8 收窄为 template-only 产品原则、权责边界、信息纪律和
    验收原则。

Template-only implementation plan 已完成；`S4-01` 至 `S4-06` 已实现、
通过独立复核并由用户本地验收。`S4-07` 已完成生产实现、真实浏览器
E2E、独立复核和完整交付门禁，用户验收单独记录。当前最终本地门禁为
backend `640 passed, 10 skipped`、latency `8 passed, 2 skipped`、
Vitest `166 passed`、Playwright `14 passed`。生产 `SEAT_PROMPT`
由 `RoomActor` 从领域 outbox 生成，浏览器证据直接来自真实 seat 消息，
不再使用 E2E-only injection。Next Step 是 provider/LLM 重入的独立
版本化设计，不在 S4-07 范围内。

## S3

- [S3 前端产品宪法 v1.1](2026-09-25-s3-frontend-constitution.md)
  - 状态：`frozen`
  - 用户已于 2026-09-25 接受五项决策。
- [S3 前端与协议预检设计 v1.1](2026-09-25-s3-frontend-design.md)
  - 状态：`frozen`
  - 描述 display actor、S3-P0 协议、SPA 路由、会话、测试与交付边界；
    已于 2026-09-25 经最终独立只读复核 `PASS` 后冻结。

实现必须同时读取本索引和上述规格。规格变更必须先于代码变更。
