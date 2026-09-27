# 规格索引

当前冻结基线为验证矩阵 v1.5.3 / S4 design v1.1.3 / S4 constitution
v1.1：

S1、S2、S3 历史基线由 v1.3 提供；v1.4 新增 LLM 路径候选规格；v1.5
根据 provider 失败结果把当前 S4 收窄为 template-only，并将 provider
条款标记为 deferred；v1.5.1 增加模板键、unused fact、admission 和
确定性验收补强。v1.5.3 / v1.1.3 统一 DM wire transport 序列，并增加
投递失败 trace。
查询修订为结构化 `TemplateVariantKey -> select()`，并把 SHA-256 digest
降为独立纯函数，只用于 trace、日志关联和跨实例校验。

1. [产品宪法](2026-09-23-product-constitution.md)
2. [RulePack v1](2026-09-23-rulepack-v1.md)
3. [系统设计](2026-09-23-system-design.md)
4. [验证矩阵 v1.5.3](2026-09-23-verification-matrix.md)
5. [S2 实时接口层设计 v1.2](2026-09-24-s2-realtime-interface-design.md)
6. [S3 前端产品宪法 v1.1](2026-09-25-s3-frontend-constitution.md)
7. [S3 前端与协议预检设计 v1.1](2026-09-25-s3-frontend-design.md)
8. [S4 AI DM 设计 v1.1.3](2026-09-26-s4-ai-dm-design.md)
9. [S4 AI DM 产品宪法 v1.1](2026-09-26-s4-ai-dm-constitution.md)

## S4 Current

- [验证矩阵 v1.5.3](2026-09-23-verification-matrix.md)
  - 状态：`frozen`
  - 当前 S4 只验收模板路由、渲染、隐私、outbox、恢复和延迟；
    provider/LLM 行标记为 deferred。
- [S4 AI DM 设计 v1.1.3](2026-09-26-s4-ai-dm-design.md)
  - 状态：`frozen`
  - 描述 template-only registry、renderer、service、metrics、有序 outbox
    和未来 provider 重入附录。
- [S4 AI DM 产品宪法 v1.1](2026-09-26-s4-ai-dm-constitution.md)
  - 状态：`frozen`
  - 将 D1 至 D8 收窄为 template-only 产品原则、权责边界、信息纪律和
    验收原则。

Template-only implementation plan 已存在并完成重基线；`S4-01` 至
`S4-04A` 已获用户验收。Next Step 是从 `codex/s4-04a-transport` 最新
交接 tip 另开授权会话执行 `S4-05 Template Metrics and Trace Privacy`；
`S4-06` 仍不得提前开始。

## S3

- [S3 前端产品宪法 v1.1](2026-09-25-s3-frontend-constitution.md)
  - 状态：`frozen`
  - 用户已于 2026-09-25 接受五项决策。
- [S3 前端与协议预检设计 v1.1](2026-09-25-s3-frontend-design.md)
  - 状态：`frozen`
  - 描述 display actor、S3-P0 协议、SPA 路由、会话、测试与交付边界；
    已于 2026-09-25 经最终独立只读复核 `PASS` 后冻结。

实现必须同时读取本索引和上述规格。规格变更必须先于代码变更。
