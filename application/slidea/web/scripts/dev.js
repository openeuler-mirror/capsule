// web/ 统一静态服务器(零依赖,基于 Node 内置模块)
// 作用:serve 整个 web/ 目录,让两个独立站点能通过 HTML 里写死的跨站绝对路径互跳。
//
// HTML 里的跨站链接约定:
//   cloud-site/index.html: <a href="/web/site/">开源项目</a>
//   site/index.html:       const DEMO_URL = "/web/cloud-site/";
// 但文件系统真实结构是 web/site/、web/cloud-site/(没有多一层 web/)。
// 所以本服务器做路径别名:/web/<site>/*  →  <site>/*
//
// 启动:
//   cd application/slidea/web && node scripts/dev.js --port 30010 --host 0.0.0.0
//   浏览器打开 http://localhost:30010/  → 默认进 cloud-site
const http = require("http");
const fs = require("fs");
const path = require("path");

// 解析 CLI 透传的 --port / --host
function argValue(name, fallback) {
  const i = process.argv.findIndex((a) => a === `--${name}` || a.startsWith(`--${name}=`));
  if (i === -1) return fallback;
  const eq = process.argv[i].indexOf("=");
  if (eq !== -1) return process.argv[i].slice(eq + 1);
  return process.argv[i + 1] || fallback;
}

const PORT = Number(argValue("port", process.env.PORT || 30010));
const HOST = argValue("host", process.env.HOST || "0.0.0.0");

// web/ 根目录 = scripts/ 的上一级
const ROOT = path.resolve(__dirname, "..");

// 跨站前缀 → 真实子目录的映射(剥掉 /web/ 前缀)
// HTML 写 /web/site/  →  文件系统 ROOT/site/
// HTML 写 /web/cloud-site/  →  文件系统 ROOT/cloud-site/
const PREFIX_ALIAS = {
  "/web/site": "/site",
  "/web/cloud-site": "/cloud-site",
};

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".svg": "image/svg+xml",
  ".webp": "image/webp",
  ".mp4": "video/mp4",
  ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ".woff2": "font/woff2",
  ".ico": "image/x-icon",
};

// 把请求路径解析成实际文件路径,应用跨站前缀别名
function resolveFile(urlPath) {
  // 根路径 → 默认进 cloud-site
  if (urlPath === "/" || urlPath === "") {
    return path.join(ROOT, "cloud-site", "index.html");
  }

  // 跨站前缀别名:精确匹配或前缀匹配 /web/<site>/...
  for (const [prefix, real] of Object.entries(PREFIX_ALIAS)) {
    if (urlPath === prefix || urlPath === prefix + "/") {
      return path.join(ROOT, real, "index.html");
    }
    if (urlPath.startsWith(prefix + "/")) {
      const rest = urlPath.slice(prefix.length); // 保留开头的 /
      return path.normalize(path.join(ROOT, real, rest));
    }
  }

  // 其他路径直接按 ROOT 解析(兼容直接访问 /site/... /cloud-site/...)
  return path.normalize(path.join(ROOT, urlPath));
}

const server = http.createServer((req, res) => {
  let urlPath = decodeURIComponent(req.url.split("?")[0]);
  const filePath = resolveFile(urlPath);

  // 防目录穿越
  if (!filePath.startsWith(ROOT)) {
    res.writeHead(403);
    res.end("Forbidden");
    return;
  }

  // 目录 → 自动找 index.html
  fs.stat(filePath, (err, stat) => {
    if (err || !stat.isFile()) {
      // 如果是目录,尝试拼 index.html
      if (!err && stat.isDirectory()) {
        const idx = path.join(filePath, "index.html");
        fs.stat(idx, (e2, s2) => {
          if (!e2 && s2.isFile()) {
            serveFile(res, idx);
            return;
          }
          notFound(res, urlPath);
        });
        return;
      }
      notFound(res, urlPath);
      return;
    }
    serveFile(res, filePath);
  });
});

function serveFile(res, filePath) {
  res.writeHead(200, {
    "Content-Type": MIME[path.extname(filePath).toLowerCase()] || "application/octet-stream",
    "Cache-Control": "no-cache",
  });
  fs.createReadStream(filePath).pipe(res);
}

function notFound(res, urlPath) {
  res.writeHead(404);
  res.end("Not Found: " + urlPath);
}

server.listen(PORT, HOST, () => {
  console.log(`web/ 统一静态服务器已启动: http://localhost:${PORT}/`);
  console.log(`  cloud-site: http://localhost:${PORT}/  或 /web/cloud-site/`);
  console.log(`  site:       http://localhost:${PORT}/web/site/`);
});
