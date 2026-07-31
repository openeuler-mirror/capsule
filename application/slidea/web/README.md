# Slidea 站点（`web/`）

两个**完全独立**的对外宣传站点，各自带自己的品牌资产，各自一个端口，互不依赖。

```
web/
├── site/              开源产品主页 — AI 驱动的 PPT 生成 Skill 介绍
│   └── assets/        站点专属 + 品牌资产（favicon、5 个 logo 变体）
├── cloud-site/        Slidea Cloud 主页 — 在线版商业产品介绍
│   └── assets/        站点专属（slides/）+ 品牌资产（同 site）
└── README.md          本文件
```

## 站点定位

| 站点 | 定位 | 默认主题 | 端口 | 目标受众 |
|---|---|---|---|---|
| `site/` | 开源 slidea skill 官方主页 | 深色 | 7100 | 开发者 / Agent 用户 |
| `cloud-site/` | Slidea Cloud 商业产品主页 | 浅色 | 7101 | 非技术用户 / 企业用户 |

两个站点的代码完全独立——各自的 `index.html` + `scripts/dev.js` + `package.json` + `assets/`。品牌资产（favicon + 5 个 logo 变体）**在每个站点的 `assets/` 各有一份拷贝**，互不依赖。logo 改版时需要同步两处（或用脚本批量替换）。

## 运行（本地开发）

**零依赖**——只用 Node.js 内置模块，**不需要 `npm install`**。需要 Node.js ≥ 16。

### 开源产品主页（端口 7100）

```bash
cd application/slidea/web/site
node scripts/dev.js
# 或：npm run dev
# 浏览器打开 http://localhost:7100/
```

### Slidea Cloud 主页（端口 7101）

```bash
cd application/slidea/web/cloud-site
node scripts/dev.js
# 或：npm run dev
# 浏览器打开 http://localhost:7101/
```

### 自定义端口 / 监听地址

```bash
node scripts/dev.js --port 7200 --host 127.0.0.1
# 或：PORT=7200 HOST=127.0.0.1 node scripts/dev.js
```

### 同时跑两个站点

端口已默认错开（7100 + 7101），直接同时启动即可：

```bash
cd application/slidea/web/site && node scripts/dev.js &
cd application/slidea/web/cloud-site && node scripts/dev.js &
```

## dev 服务器作用域

**每个 dev 服务器只服务自己的 `web/<site>/` 目录**，不向上 serve 整个 `application/slidea/`。

| 访问 URL | dev 服务器响应 |
|---|---|
| `http://localhost:<port>/` | 返回 `<site>/index.html` |
| `http://localhost:<port>/assets/<file>` | 返回 `<site>/assets/<file>` |
| 其他 | 404 或对应文件 |

跨 `web/` 边界的引用（例如 `site/index.html` 里的 `/docs/example/...`）在本地 dev 时会 404。如果需要这些跨目录资源，要么把样例拷进 `<site>/assets/`，要么用一个能服务整个 `application/slidea/` 的服务器（比如 `python3 -m http.server` 在 `application/slidea/` 下启动）。

## 资产引用规则

| 资产类型 | 引用方式 | 例子 |
|---|---|---|
| 品牌资产（favicon、logos） | 相对路径 `assets/X.png` | `<img src="assets/logo-icon.png">` |
| 站点专属资产 | 相对路径 `assets/X.png` | `<img src="assets/slides/page-01.png">`（在 cloud-site 内） |
| 跨站导航 | 绝对路径 `/web/site/` 或 `/web/cloud-site/` | `<a href="/web/site/">开源项目</a>` |

**跨站导航链接在本地 dev 会 404**（每个 dev 服务器只知道自己）。在生产部署时，如果两个站点部署在同一服务器的 `/web/site/` 和 `/web/cloud-site/` 路径下，链接正常工作；如果部署到不同域名，需要把链接改成完整 URL（如 `https://slidea.cloud`）。

## 部署

两个站点**完全独立**，可以分别部署到不同服务器、不同域名。每个站点是自己的部署单元，不需要把 `web/` 整个上。

部署检查清单（每个站点）：
- [ ] 静态文件服务器（nginx / Apache / CDN）能服务 `<site>/` 目录
- [ ] 站点的 `<site>/assets/` 完整上载（含 6 个品牌资产 + 站点专属资产）
- [ ] 跨站导航链接（如果有）改成生产域名

## 修改品牌资产

每个站点的 `assets/` 下都有这 6 个品牌资产（独立拷贝）：

- `favicon.png` — 浏览器标签图标
- `logo-icon.png` — Slidea 图标（无文字）
- `logo-wordmark-dark.png` — 深色背景下的 logo 文字
- `logo-wordmark-light.png` — 浅色背景下的 logo 文字
- `logo-full.png` — 完整 logo（图标 + 文字，浅色背景）
- `logo-full-dark.png` — 完整 logo（图标 + 文字，深色背景）

logo 改版时**两个站点都要改**（各自独立维护一份）。可以用脚本批量替换：

```bash
NEW_LOGO=/path/to/new-logo-icon.png
cp "$NEW_LOGO" web/site/assets/logo-icon.png
cp "$NEW_LOGO" web/cloud-site/assets/logo-icon.png
```
