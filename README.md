# Werewolf DM

[![CI](https://github.com/Ma-Z-Y/ai-werewolf-dm/actions/workflows/ci.yml/badge.svg)](https://github.com/Ma-Z-Y/ai-werewolf-dm/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12-3776AB.svg)](https://www.python.org/)

面向 6 人线下或局域网朋友局的 AI 狼人杀 DM。玩家使用手机加入，共享屏承载
公共舞台；AI 只负责主持、裁决、叙述和引导，不补位玩家，也不替玩家推理。

## 项目状态

项目正在分阶段构建，当前公开基线不是生产版本：

- S1 Headless 规则核心：已完成并通过独立验收。
- S2 单进程 FastAPI/WebSocket 实时接口层：S2-01 至 S2-13 已完成、合并并
  通过最终跨层验收修复。
- S3 前端与 S4 AI DM：尚未开始。

准确状态和下一步以
[`docs/specs/README.md`](docs/specs/README.md) 与
[`docs/ROADMAP.md`](docs/ROADMAP.md) 为准。

## 核心原则

- 游戏状态只能通过领域层 `apply_command()` 改变。
- LLM 不拥有状态，也不能绕过规则核心修改游戏状态。
- 服务端严格区分公共信息、座位私有信息和主持人控制信息。
- 所有公开契约、状态转移和错误响应均使用严格模型并进行确定性验证。
- 领域层不依赖 FastAPI、WebSocket、数据库、墙钟时间或全局随机数。

## 快速开始

要求 Python 3.12。

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

运行完整验证：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q -m latency
.\.venv\Scripts\python.exe -m ruff check --no-cache src tests
.\.venv\Scripts\python.exe -m ruff format --check src tests
.\.venv\Scripts\python.exe -m mypy src
```

启动本地 API：

```powershell
.\.venv\Scripts\python.exe -m uvicorn `
  werewolf_dm.interfaces.http_ws.app:create_app `
  --factory --host 127.0.0.1 --port 8000 --reload
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/healthz
```

## 仓库结构

```text
backend/                       Python 3.12 后端与测试
  src/werewolf_dm/domain/      纯领域模型、状态机、投影与回放
  src/werewolf_dm/application/ 游戏内核、回放、模拟与房间运行时
  src/werewolf_dm/interfaces/  FastAPI/WebSocket/REST 适配层
  tests/                       单元、集成和无头确定性回归
docs/specs/                    产品宪法、规则、系统设计与验证矩阵
docs/ARCHITECTURE.md           面向贡献者的架构入口
docs/ROADMAP.md                分阶段路线图
```

更完整的领域设计从
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) 开始。

## 参与贡献

提交前请阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md)。安全问题请按照
[`SECURITY.md`](SECURITY.md) 私密报告，不要创建公开 Issue。

## 许可证

Apache License 2.0，详见 [`LICENSE`](LICENSE)。
