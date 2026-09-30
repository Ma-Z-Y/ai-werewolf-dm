# 单人版并入与 Pages 视觉升级设计

**状态：** `v0.1.0 accepted`

**日期：** 2026-09-30

**已确认方向：** 用户选择方案 A。单人优化版源码并入本仓库，GitHub Pages
同时提供项目主页和可玩的 `/solo/` 入口；不把单人版移植成现有 React 六人
前端的内部路由。

## 1. 目标

把 `D:\VibeCoding\codex\ai-werewolf-solo` 从独立衍生项目升级为
`Ma-Z-Y/ai-werewolf-dm` 仓库内的一级子应用，并重做 GitHub Pages
项目主页，使访问者能在同一产品叙事下：

1. 立即进入单人 12 人离线规则 AI 对局。
2. 理解六人朋友局的 AI DM 定位、部署方式和架构边界。
3. 在桌面和手机上获得一致、完整、接近成熟互联网产品的主页体验。

主页的目标不是堆叠装饰，而是让真实可玩内容、真实游戏画面和清晰的产品
分层成为视觉主体。

## 2. 非目标

- 不把 `ai-werewolf-solo` 的 Three.js、游戏循环和 UI 改写成 React。
- 不把单人规则引擎与六人 `GameCore` 强行合并为同一状态机。
- 不改变六人后端、WebSocket 协议、SQLite 持久化和现有前端功能。
- 不在 GitHub Pages 运行 FastAPI、WebSocket 或 SQLite。
- 不在本轮 push、创建 PR、merge、tag 或 release，除非用户另行授权。

## 3. 仓库边界

### 3.1 `solo/`

新增本仓库一级目录 `solo/`，内容来自单人优化版本地仓库
`codex/optimize-offline-ux@e9aa0c8`，但排除以下内容：

- `.git/`
- `node_modules/`
- `dist/`
- `.env`
- `.env.local`
- `artifacts/`
- 本地缓存和未纳入交付的生成物

保留并明确：

- `solo/LICENSE` 保留上游 MIT 许可。
- `solo/README.md` 说明当前来源、优化增量、开发命令和仓库内定位。
- 新增 `solo/UPSTREAM.md`，记录上游仓库、基线提交、本地优化提交和
  MIT 归属。
- `solo/vite.config.ts` 继续使用相对资源路径 `base: './'`，确保
  `/solo/` 子路径部署可用。

`solo/` 是单人模式唯一权威源码。原独立目录不再作为后续修改入口，但在
用户确认合并结果前不删除、不重写。

### 3.2 `site/`

现有 `site/` 保持 GitHub Pages 入口和项目主页职责，但升级为可构建的
静态站点：

- `site/package.json`：固定站点自身依赖和脚本。
- `site/package-lock.json`：锁定依赖。
- `site/vite.config.ts`：静态构建配置。
- `site/index.html`：主页语义结构。
- `site/src/site.ts`：首屏、滚动编排和轻量交互。
- `site/src/site.css`：主页视觉系统和响应式布局。
- `site/public/assets/`：保留和补充真实产品截图。
- `site/tools/prepare-pages.mjs`：把单人版构建产物放入站点输出目录的
  `solo/` 子路径。

`site/` 不承载运行时 API，也不代替 FastAPI 服务。

### 3.3 构建产物

本地和 CI 都不提交以下生成物：

- `solo/dist/`
- `site/dist/`

GitHub Pages 的最终发布目录由 `site/dist/` 生成，其中：

```text
site/dist/
  index.html
  assets/
  solo/
    index.html
    assets/
    manifest.webmanifest
    sw.js
```

## 4. 技术方案

### 4.1 单人版

单人版继续使用现有 Vite、TypeScript 和 Three.js 0.186.x，不迁移框架。
以下既有能力必须保持：

- 一键离线开局。
- 自动存档和继续上次对局。
- PWA manifest 和 service worker。
- 规则 AI 和可选 OpenAI 兼容 provider。
- 桌面与手机布局。
- 84 项现有测试。

构建只需改变产物进入 Pages 的位置，不改变游戏协议或存档格式。

### 4.2 主页动效

主页使用 `Motion 13.4.6`，MIT 许可，来源与官方文档如下：

- https://github.com/motiondivision/motion
- https://motion.dev/docs/quick-start
- https://motion.dev/docs/scroll

选择理由：

- JavaScript 原生 API 可直接用于当前 Vite 静态站点。
- 支持 `animate()`、`inView()` 和 `scroll()`，无需引入 React 运行时。
- 公开包体积和滚动实现适合 GitHub Pages。
- 与现有 CSS 和真实截图组合，不需要重写为组件库。

不引入 React Three Fiber、MagicUI 或完整页面模板。单人版已经提供真实
Three.js 游戏场景，主页只借用 Motion 做编排，避免第二套 3D 运行时和
模板化组件依赖。

### 4.3 视觉方向

主页采用“雾镇档案和真实对局”的产品视觉，不使用通用紫色渐变、装饰
光球、模糊背景或无意义粒子。

视觉原则：

- 首屏使用真实游戏标题场景或对局大图，而不是把截图装进小窗口卡片。
- 标题保持产品或玩法名称，不使用营销口号替代产品名。
- 深色主背景使用冷黑、雾灰、月光蓝和骨白，危险红只用于关键动作。
- 金色只保留少量品牌和状态强调，避免整页单一棕金色调。
- 字体使用本机可用的中文字体栈，避免强依赖外网字体。
- 桌面使用不对称大画面和横向节奏，手机使用单列、整屏主视觉和
  44 px 以上触控目标。
- 所有入场和滚动动效必须受 `prefers-reduced-motion` 控制。

### 4.4 首屏交互

桌面首屏：

- 全宽真实游戏画面作为主视觉。
- 鼠标移动只改变雾层或画面裁切位置，不改变布局尺寸。
- 主 CTA 为“单人试玩”，次 CTA 为“六人朋友局”。
- 首屏下方保留下一节内容的可见边缘，避免独立“宣传页”感觉。

移动端首屏：

- 使用竖屏真实对局画面或桌面画面裁切。
- 关闭鼠标跟随，只保留一次性入场和静态雾层。
- CTA 与状态信息不得遮挡角色、标题或按钮。

### 4.5 主页内容结构

1. 导航和滚动进度。
2. 首屏：产品名、单人试玩、六人朋友局、真实游戏画面。
3. 双模式对比：单人 1 对 11 与六人 1 个 AI DM。
4. 玩法证明：夜间行动、警长竞选、颁奖典礼的真实截图。
5. 工程能力：规则裁判、信息隔离、恢复与持久化。
6. 在线交付：Docker Compose、单容器、同源静态资源。
7. 架构和开源边界：单人 MIT 衍生、六人 Apache-2.0、上游归属。
8. 结尾 CTA：进入单人试玩或打开 GitHub。

## 5. Pages 构建流程

`.github/workflows/pages.yml` 调整为：

1. Checkout。
2. 安装 Node 24。
3. 在 `solo/` 执行 `npm ci`、`npm test`、`npm run typecheck` 和
   `npm run build`。
4. 在 `site/` 执行 `npm ci` 和 `npm run build`。
5. 执行 `site/tools/prepare-pages.mjs`，把 `solo/dist/` 复制到
   `site/dist/solo/`。
6. 配置并发布 `site/dist/`。

触发路径增加：

- `solo/**`
- `site/**`
- `.github/workflows/pages.yml`

README 链接和主页 CTA 指向：

- 主页：`https://ma-z-y.github.io/ai-werewolf-dm/`
- 单人试玩：`https://ma-z-y.github.io/ai-werewolf-dm/solo/`

## 6. 错误与降级

- `solo/` 构建失败时 Pages 不发布半成品，保留上一版线上页面。
- `site/` 或 Motion 加载失败时，主页仍保留可读 HTML、截图和链接。
- JavaScript 关闭时，导航、截图、模式说明和两个主要链接仍可访问。
- service worker 只在单人版目录内注册，不接管项目主页。
- 单人版本地存档键不改变，避免并入仓库后丢失已有存档。
- 若相对资源路径在 `/solo/` 下验证失败，先修正 Vite/资源 URL，
  禁止通过复制到根目录掩盖问题。

## 7. 验证

### 7.1 自动验证

`solo/`：

- `npm test`
- `npm run typecheck`
- `npm run build`
- 验证构建产物中 `sw.js`、`manifest.webmanifest` 和资源路径均为相对路径。

`site/`：

- `npm run build`
- 验证 `site/dist/index.html`、`site/dist/assets/` 和
  `site/dist/solo/index.html` 存在。
- 运行轻量静态检查，确保主页不引用不存在的外部本地资源。

仓库级：

- 更新 `scripts/verify-delivery.ps1`，把 `solo/` 与 `site/` 的构建和
  测试纳入交付门禁。
- CI 增加独立的 static-site job，避免只验证 backend 和六人 frontend。

### 7.2 浏览器验证

使用真实 Chromium 检查：

- 桌面 `1440x900`。
- 手机 `390x844`。
- 无水平滚动。
- 首屏主图加载完成。
- 导航和 CTA 可见、可点击。
- 单人 CTA 能打开 `/solo/` 并显示标题页。
- 一键离线开局可以进入游戏，不要求 API Key。
- 页面切到后台再回来不会破坏单人版自动存档。
- `prefers-reduced-motion: reduce` 下无持续滚动或视差动画。

### 7.3 发布验证

发布 workflow 成功后：

- 主页返回 HTTP 200。
- `/solo/` 返回 HTTP 200。
- `/solo/sw.js` 返回 HTTP 200。
- 页面主图和关键截图返回 HTTP 200。
- README 的在线试玩链接与实际地址一致。

## 8. 验收标准

满足以下全部条件才可交付：

1. `solo/` 已进入本仓库并保留 MIT 许可与上游归属。
2. 主页由真实游戏画面和内容构成，而不是仅给现有截图加动效。
3. 桌面和手机均呈现完整首屏、清晰模式选择和可用 CTA。
4. `/solo/` 可独立加载、离线开局、继续存档和注册 PWA。
5. 六人后端和现有 React 前端行为不变。
6. `solo/` 测试、站点构建、仓库交付门禁和浏览器检查通过。
7. 没有把包体积较大的第二套 3D 或 UI 框架加到主页。
8. 未经明确授权，不 push、不创建 PR、不 merge、不 tag、不 release。

## 9. 风险与回滚

主要风险：

- 单人版绝对路径或 service worker scope 在 `/solo/` 下失效。
- 两份 `package-lock.json` 增加 CI 安装时间。
- 视频式滚动动效可能损害移动端性能和可访问性。
- 复制上游源码时误带 `.env` 或其他本地秘密。

缓解：

- 以生产构建后的相对路径和 service worker scope 实测为准。
- 只在 Pages workflow 需要时安装两个子项目依赖，主前端 CI 不引入。
- 动效只使用 transform、opacity 和有限滚动绑定，并遵守 reduced motion。
- 复制前按允许列表筛选，复制后检查不存在 `.env`、token 或本地产物。

回滚：

- 所有变更保留在 `codex/integrated-solo-pages` 本地分支。
- 回滚时移除 `solo/`、恢复 `site/` 和 workflow 的旧版本即可。
- 原独立单人版目录在验收前保持不变，作为可恢复来源。

## 10. 实施顺序

1. 并入 `solo/` 并建立归属文件。
2. 验证单人版本地测试、构建和 `/solo/` 子路径。
3. 建立 `site/` Vite 构建和 Pages 合成脚本。
4. 重做主页视觉、内容和 Motion 编排。
5. 更新 CI、交付门禁、README 和公开文案。
6. 完成桌面、手机、离线、PWA 和发布产物验证。
