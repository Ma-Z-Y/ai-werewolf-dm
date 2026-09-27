# 规格索引

当前冻结基线为验证矩阵 v1.5 / S4 design v1.1 / S4 constitution v1.1：

S1、S2、S3 历史基线由 v1.3 提供；v1.4 新增 LLM 路径候选规格；v1.5
根据 provider 失败结果把当前 S4 收窄为 template-only，并将 provider
条款标记为 deferred。

1. [产品宪法](2026-09-23-product-constitution.md)
2. [RulePack v1](2026-09-23-rulepack-v1.md)
3. [系统设计](2026-09-23-system-design.md)
4. [验证矩阵 v1.5](2026-09-23-verification-matrix.md)
5. [S2 实时接口层设计 v1.2](2026-09-24-s2-realtime-interface-design.md)
6. [S3 前端产品宪法 v1.1](2026-09-25-s3-frontend-constitution.md)
7. [S3 前端与协议预检设计 v1.1](2026-09-25-s3-frontend-design.md)
8. [S4 AI DM 设计 v1.1](2026-09-26-s4-ai-dm-design.md)
9. [S4 AI DM 产品宪法 v1.1](2026-09-26-s4-ai-dm-constitution.md)

## S4 Frozen

- [验证矩阵 v1.5](2026-09-23-verification-matrix.md)
  - 状态：`frozen`
  - 当前 S4 只验收模板路由、渲染、隐私、outbox、恢复和延迟；
    provider/LLM 行标记为 deferred。
- [S4 AI DM 设计 v1.1](2026-09-26-s4-ai-dm-design.md)
  - 状态：`frozen`
  - 描述 template-only registry、renderer、service、metrics、有序 outbox
    和未来 provider 重入附录。
- [S4 AI DM 产品宪法 v1.1](2026-09-26-s4-ai-dm-constitution.md)
  - 状态：`frozen`
  - 将 D1 至 D8 收窄为 template-only 产品原则、权责边界、信息纪律和
    验收原则。

S4 template-only 规格已冻结，但 implementation plan 和实现代码仍需
后续明确授权。

## S3

- [S3 前端产品宪法 v1.1](2026-09-25-s3-frontend-constitution.md)
  - 状态：`frozen`
  - 用户已于 2026-09-25 接受五项决策。
- [S3 前端与协议预检设计 v1.1](2026-09-25-s3-frontend-design.md)
  - 状态：`frozen`
  - 描述 display actor、S3-P0 协议、SPA 路由、会话、测试与交付边界；
    已于 2026-09-25 经最终独立只读复核 `PASS` 后冻结。

实现必须同时读取本索引和上述规格。规格变更必须先于代码变更。
