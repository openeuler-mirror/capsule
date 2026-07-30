// Slidea Cloud 产品官网本地开发服务器（零依赖，基于 Node 内置模块）
// 从 application/slidea/web/cloud-site/ 目录启动，向上服务整个 application/slidea，
// 这样页面可以引用 /web/shared/assets 中的共享品牌素材。
const http = require("http");
const fs = require("fs");
const path = require("path");

// 解析 CLI 透传的 --port / --host（兼容 --port=xxxx 与 --port xxxx 两种写法）
function argValue(name, fallback) {
  const i = process.argv.findIndex((a) => a === `--${name}` || a.startsWith(`--${name}=`));
  if (i === -1) return fallback;
  const eq = process.argv[i].indexOf("=");
  if (eq !== -1) return process.argv[i].slice(eq + 1);
  return process.argv[i + 1] || fallback;
}

const PORT = Number(argValue("port", process.env.PORT || 7100));
const HOST = argValue("host", process.env.HOST || "0.0.0.0");

// application/slidea 根目录 = web/cloud-site/scripts/ 的上三级
const SITE_DIR = path.resolve(__dirname, "..");
const ROOT = path.resolve(SITE_DIR, "..", "..");

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

const server = http.createServer((req, res) => {
  let urlPath = decodeURIComponent(req.url.split("?")[0]);

  // 站点入口
  if (urlPath === "/" || urlPath === "/index.html" || urlPath === "/web/cloud-site" || urlPath === "/web/cloud-site/" || urlPath === "/web/cloud-site/index.html") {
    urlPath = "/web/cloud-site/index.html";
  }

  const filePath = path.normalize(path.join(ROOT, urlPath));
  if (!filePath.startsWith(ROOT)) {
    res.writeHead(403);
    res.end("Forbidden");
    return;
  }

  fs.stat(filePath, (err, stat) => {
    if (err || !stat.isFile()) {
      res.writeHead(404);
      res.end("Not Found: " + urlPath);
      return;
    }
    res.writeHead(200, {
      "Content-Type": MIME[path.extname(filePath).toLowerCase()] || "application/octet-stream",
      "Cache-Control": "no-cache",
    });
    fs.createReadStream(filePath).pipe(res);
  });
});

server.listen(PORT, HOST, () => {
  console.log(`Slidea Cloud 产品官网开发服务器已启动: http://localhost:${PORT}/`);
});
