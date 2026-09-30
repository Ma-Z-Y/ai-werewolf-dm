# 在线部署

这个部署方式把前端静态文件、FastAPI API、WebSocket 和 SQLite 数据放进一个容器。玩家不需要安装任何软件，只需要打开一个 URL。

## 一条命令启动

要求：

- Docker Engine
- Docker Compose v2

在项目根目录运行：

```powershell
docker compose up -d --build
```

打开：

```text
http://127.0.0.1:8000
```

数据库保存在命名卷 `werewolf_data` 中。停止或重建容器不会删除房间数据。

```powershell
docker compose down
```

如果要连同数据一起清理，只有在确认不需要房间和审计数据后才运行：

```powershell
docker compose down -v
```

## 临时公网地址

适合先让朋友验证“打开即玩”的体验。服务器本机需要保持开机。

1. 启动容器：

```powershell
docker compose up -d --build
```

2. 使用 Cloudflare Tunnel 把本机 8000 端口发布为临时 HTTPS 地址：

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

3. 把 Cloudflare 输出的 HTTPS 地址发给朋友。

主持人创建房间后，页面会显示玩家邀请二维码和 `/join/<房间码>` 链接。玩家扫码或打开链接即可。

临时地址适合验证，不适合长期公开运营。地址可能变化，而且服务电脑必须保持在线。

## 固定域名

正式使用建议把容器放到一台常驻服务器上，再用 HTTPS 反向代理指向 `127.0.0.1:8000`。

### Caddy 示例

```caddyfile
play.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Caddy 会自动申请 HTTPS 证书，并支持 WebSocket 转发。

### 注意事项

- 公网必须使用 HTTPS，浏览器才能稳定使用 WebSocket 和剪贴板能力。
- SQLite 只适合单实例。不要同时启动多个共享同一数据库文件的容器。
- 保留 `/app/data` 持久卷；不要只备份镜像。
- 如果服务器位于中国大陆，域名和接入方式需要按云厂商要求完成合规流程。
- 当前版本没有公开账号体系、公开匹配和支付。建议只给朋友群或受邀用户使用。

## 健康检查

```powershell
Invoke-RestMethod http://127.0.0.1:8000/healthz
```

容器内置健康检查。也可以运行：

```powershell
docker inspect --format "{{.State.Health.Status}}" $(docker compose ps -q app)
```

## 环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `WEREWOLF_DM_DB_PATH` | `/app/data/werewolf_dm.sqlite3` | SQLite 数据文件 |
| `WEREWOLF_DM_STATIC_DIR` | `/app/static` | 前端构建产物目录 |

## 更新

```powershell
git pull
docker compose up -d --build
```

## Docker Hub 超时回退

如果 `docker compose up -d --build` 在拉取 `node:24-bookworm-slim` 或
`python:3.12-slim` 时出现 `auth.docker.io` 超时，可使用已经安装好的
项目环境直接运行同源生产模式：

```powershell
cd D:\VibeCoding\codex\Codex_02\backend
$env:WEREWOLF_DM_STATIC_DIR="D:\VibeCoding\codex\Codex_02\frontend\dist"
$env:WEREWOLF_DM_DB_PATH="$env:TEMP\werewolf-dm-local.sqlite3"
.\.venv\Scripts\python.exe -m uvicorn `
  werewolf_dm.interfaces.http_ws.app:create_app `
  --factory --host 127.0.0.1 --port 8000
```

该回退不替代正式容器部署，只用于本机预览和 Docker Hub 网络异常时的
临时运行。

## 本地开发仍然可用

开发时仍可以分别运行 FastAPI 和 Vite：

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn werewolf_dm.interfaces.http_ws.app:create_app --factory --host 127.0.0.1 --port 8000
```

```powershell
cd frontend
pnpm dev
```

开发地址为 `http://127.0.0.1:5173`。生产容器使用同一个 8000 端口提供前端和 API。
