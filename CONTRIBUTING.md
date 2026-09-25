# 参与贡献

感谢参与 Werewolf DM。项目优先保证规则正确性、确定性、信息隔离和安全边界。

## 开始之前

1. 阅读 [`README.md`](README.md)、[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
   和 [`docs/specs/README.md`](docs/specs/README.md)。
2. 对行为变化先创建 Issue，说明问题、范围、验收标准和明确不做的内容。
3. 小型修复可直接提交 PR；大型功能应先讨论设计，避免在错误方向上扩大实现。

## 开发环境

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## 变更要求

- 优先修复根因，不在多个调用点复制临时补丁。
- 游戏状态只能通过 `apply_command()` 变化。
- 新增或修正行为必须先有失败回归，再实现最小修复。
- 不引入未使用的抽象、依赖、配置或脚手架。
- 公开接口、错误码、事件和状态模型必须保持严格且可验证。
- 不提交密钥、令牌、个人数据、虚拟环境、缓存、构建产物或本地路径。

## 提交前验证

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check --no-cache src tests
.\.venv\Scripts\python.exe -m ruff format --check src tests
.\.venv\Scripts\python.exe -m mypy src
```

PR 必须包含：

- 变更动机和用户可观察结果。
- 实际执行的测试与结果。
- 风险、兼容性和未覆盖项。
- 关联 Issue；安全漏洞不得公开提交。

## Commit 与 PR

- 使用聚焦的小提交，推荐 `feat:`、`fix:`、`test:`、`docs:`、
  `refactor:`、`chore:`、`ci:` 前缀。
- 每个 PR 只解决一个清晰问题，避免混入无关格式化或批量重命名。
- 作者需响应评审意见；阻塞问题修复前不会合并。

## 许可证

提交贡献即表示你同意贡献内容按 Apache License 2.0 授权。
