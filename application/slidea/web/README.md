# Slidea 站点（`web/`）

这个目录托管 Slidea 的两个对外宣传站点。两个站点独立运行，共享品牌资产。

```
web/
├── shared/assets/     品牌资产单一源（favicon + 5 个 logo 变体）
├── site/              开源产品主页 — AI 驱动的 PPT 生成 Skill 介绍
└── cloud-site/        Slidea Cloud 主页 — 在线版商业产品介绍
```

## 站点定位

| 站点 | 定位 | 默认主题 | 目标受众 |
|---|---|---|---|
| `site/` | 开源 slidea skill 的官方主页，介绍 Skill 能力、安装方式、工作流 | 深色 | 开发者 / Agent 用户 |
| `cloud-site/` | Slidea Cloud 商业产品主页，强调"打开浏览器即用" | 浅色 | 非技术用户 / 企业用户 |
| `shared/assets/` | 不是站点，是品牌资产共享目录 | — | — |

两个站点的代码完全独立（各自的 `index.html` + `scripts/dev.js` + `package.json`），但 `cloud-site/` 通过绝对路径 `/web/shared/assets/` 引用 `shared/` 里的品牌资产，`site/` 也是同样引用方式。

## 运行（本地开发）

**零依赖**——只用 Node.js 内置模块，**不需要 `npm install`**。需要 Node.js ≥ 16。

### 开源产品主页

```bash
cd application/slidea/web/site
node scripts/dev.js
# 或：npm run dev
```

浏览器打开 `http://localhost:7100/web/site/`

### Slidea Cloud 主页

```bash
cd application/slidea/web/cloud-site
node scripts/dev.js
# 或：npm run dev
```

浏览器打开 `http://localhost:7100/web/cloud-site/`

### 自定义端口 / 监听地址

```bash
node scripts/dev.js --port 7101 --host 127.0.0.1
# 或：PORT=7101 HOST=127.0.0.1 node scripts/dev.js
```

### 同时跑两个站点

两个 dev 服务器都向上 serve 整个 `application/slidea/`，所以可以同时启动而不互相干扰，只要端口不同：

```bash
cd application/slidea/web/site && node scripts/dev.js --port 7101 &
cd application/slidea/web/cloud-site && node scripts/dev.js --port 7102 &
```

## URL 与磁盘路径对应关系

dev 服务器静态服务 `application/slidea/` 根目录，URL 直接反映磁盘路径：

| URL | 磁盘路径 |
|---|---|
| `/web/site/` | `application/slidea/web/site/index.html` |
| `/web/cloud-site/` | `application/slidea/web/cloud-site/index.html` |
| `/web/shared/assets/<file>` | `application/slidea/web/shared/assets/<file>` |
| `/web/cloud-site/assets/slides/<file>` | `application/slidea/web/cloud-site/assets/slides/<file>`（cloud-site 专属样片） |
| `/docs/example/...` | `application/slidea/docs/example/`（站点演示里用到的真实样例素材） |

根路径 `/` 在 `web/site/scripts/dev.js` 里重定向到 `/web/site/index.html`；在 `web/cloud-site/scripts/dev.js` 里重定向到 `/web/cloud-site/index.html`。

## 资产引用规则

| 资产类型 | 引用方式 | 例子 |
|---|---|---|
| 共享品牌资产（favicon、logos） | 绝对路径 `/web/shared/assets/X.png` | `<img src="/web/shared/assets/logo-icon.png">` |
| 各站专属资产 | 相对路径 `assets/X.png` | `<img src="assets/slides/page-01.png">`（在 cloud-site 内） |
| 跨站导航 | 绝对路径 `/web/site/` 或 `/web/cloud-site/` | `<a href="/web/site/">开源项目</a>` |

不要在站点代码里写死 `/site/assets/` 或 `/cloud-site/assets/`（旧路径已废弃）。

## 部署

`web/` 是部署单元——三个子目录必须一起部署到同一台服务器 / 同一个 CDN 根下，因为：

1. `shared/assets/` 被两个站点引用
2. 站点间的"开源项目"/"在线体验"跳转链接假设两个站点同根
3. 站点演示中引用的 `/docs/example/...` 也假设 `application/slidea/` 是同一个服务根

如果未来需要把两个站点拆到不同域名，需要：
- 把 `shared/assets/` 复制到每个站点的 `assets/` 下，并把绝对路径改相对路径
- 把跨站导航链接改成完整域名（如 `https://slidea.cloud`）
- 把 `/docs/example/` 引用换成各自部署的副本

## 修改品牌资产

`web/shared/assets/` 里的 6 个 PNG 是品牌单一源。改 logo 时只改这里，两个站点会同时更新。

当前文件清单：

- `favicon.png` — 浏览器标签图标
- `logo-icon.png` — Slidea 图标（无文字）
- `logo-wordmark-dark.png` — 深色背景下的 logo 文字
- `logo-wordmark-light.png` — 浅色背景下的 logo 文字
- `logo-full.png` — 完整 logo（图标 + 文字，浅色背景）
- `logo-full-dark.png` — 完整 logo（图标 + 文字，深色背景）
