#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
xiray 博客写作台 —— 本地服务

功能：
  1. 提供写作界面（tools/writer.html）
  2. 接收发布请求：生成文章 HTML → 更新首页列表 → git 提交 → 自动探测代理 → 推送
  3. 代理自动探测：环境变量 → Windows 系统代理 → 常见本地端口，实测连通才使用
     不往 git 配置里写死端口，只对本次命令生效

用法：
  双击 写作台.bat         启动服务并打开浏览器
  python publish.py       同上
  python publish.py --sync   只做「提交+推送」，不起服务（手动改完文件后跑这个）
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request

try:
    import winreg
except ImportError:
    winreg = None

try:
    import webbrowser
except ImportError:
    webbrowser = None

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
PORT = 5178
SITE = "https://xiray.github.io/blog"

# ---------------------------------------------------------------- 代理探测

COMMON_PORTS = [7890, 7897, 7891, 10809, 10808, 1080, 8889, 64836, 33210, 2080, 4780]

_proxy_cache = {"value": "unknown", "at": 0.0}


def _normalize(p):
    p = (p or "").strip()
    if not p:
        return None
    if "://" not in p:
        p = "http://" + p
    return p


def proxy_candidates():
    """按优先顺序收集可能的代理地址。"""
    found = []

    def add(p):
        p = _normalize(p)
        if p and p not in found:
            found.append(p)

    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        add(os.environ.get(key))

    if winreg:
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            )
            try:
                try:
                    enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
                except OSError:
                    enabled = 0
                if enabled:
                    try:
                        server = winreg.QueryValueEx(key, "ProxyServer")[0]
                    except OSError:
                        server = ""
                    if server:
                        if "=" in server:
                            for part in server.split(";"):
                                if "=" in part:
                                    add(part.split("=", 1)[1])
                        else:
                            add(server)
                            for scheme in ("http", "https"):
                                add("%s://%s" % (scheme, server))
            finally:
                winreg.CloseKey(key)
        except Exception:
            pass

    for port in COMMON_PORTS:
        add("http://127.0.0.1:%d" % port)

    return found


def test_proxy(proxy, timeout=6):
    """真的去连一次 github.com，能通才算数。"""
    try:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        opener = urllib.request.build_opener(handler)
        req = urllib.request.Request("https://github.com/login", method="HEAD")
        req.add_header("User-Agent", "xiray-blog-publish")
        with opener.open(req, timeout=timeout) as resp:
            return resp.status < 400
    except Exception:
        return False


def test_direct(timeout=8):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request("https://github.com/login", method="HEAD")
        req.add_header("User-Agent", "xiray-blog-publish")
        with opener.open(req, timeout=timeout) as resp:
            return resp.status < 400
    except Exception:
        return False


def find_proxy(force=False):
    """返回 (proxy, 说明)。proxy 为 None 表示直连。"""
    now = time.time()
    if not force and _proxy_cache["value"] != "unknown" and now - _proxy_cache["at"] < 120:
        return _proxy_cache["value"]

    cands = proxy_candidates()
    verdict = {}

    def probe(p):
        verdict[p] = test_proxy(p)

    threads = [threading.Thread(target=probe, args=(p,), daemon=True) for p in cands]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=8)

    for p in cands:
        if verdict.get(p):
            _proxy_cache["value"] = p
            _proxy_cache["at"] = now
            return p

    if test_direct():
        _proxy_cache["value"] = None
        _proxy_cache["at"] = now
        return None

    _proxy_cache["value"] = None
    _proxy_cache["at"] = now
    return None


# ---------------------------------------------------------------- git 操作


def run_git(args, timeout=120):
    cmd = ["git", "-C", ROOT] + args
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        out = (r.stdout or b"").decode("utf-8", "replace")
        err = (r.stderr or b"").decode("utf-8", "replace")
        return r.returncode, out, err
    except subprocess.TimeoutExpired:
        return 124, "", "超时"
    except Exception as e:
        return 1, "", repr(e)


def git_commit(message):
    run_git(["add", "-A"])
    code, out, err = run_git(["commit", "-m", message])
    if code == 0:
        return True, "已提交"
    blob = (out + err).lower()
    if "nothing to commit" in blob or "no changes added" in blob:
        return True, "内容无变化"
    return False, (out + err).strip()[:400]


def git_push():
    """自动选代理后推送，失败会换代理重试。"""
    last = ""
    for attempt in range(3):
        proxy = find_proxy(force=(attempt > 0))
        if proxy:
            cfg = ["-c", "http.proxy=" + proxy, "-c", "https.proxy=" + proxy]
        else:
            cfg = ["-c", "http.proxy=", "-c", "https.proxy="]
        code, out, err = run_git(cfg + ["push"], timeout=150)
        if code == 0:
            return True, ("经由 " + proxy) if proxy else "直连", out.strip()
        last = (err or out).strip()
        time.sleep(2)
    return False, "", last[:500]


# ---------------------------------------------------------------- 文章生成

ARTICLE = u"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — xiray</title>
<meta name="description" content="{excerpt}">
<meta name="color-scheme" content="dark light">
<link rel="stylesheet" href="../assets/style.css">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><circle cx='50' cy='50' r='38' fill='%23ffb86b'/></svg>">
</head>
<body>

<header class="site-header">
  <div class="wrap">
    <a class="brand" href="../index.html">xiray</a>
    <nav class="nav">
      <a href="../index.html">文章</a>
      <a href="../about.html">关于</a>
      <a href="https://github.com/xiray" target="_blank" rel="noopener">GitHub</a>
    </nav>
  </div>
</header>

<main class="wrap">
  <article class="article">

    <header class="article-header">
      <a class="back-link" href="../index.html">← 返回文章列表</a>
      <h1>{title}</h1>
      <p class="article-meta">{date_dots} <span class="dot">·</span> 约 {minutes} 分钟</p>
    </header>

    <div class="prose">
{body}
    </div>
  </article>
</main>

<footer class="site-footer">
  <div class="wrap">
    <span>© 2026 xiray</span>
    <span><a href="../index.html">回到首页</a></span>
  </div>
</footer>

</body>
</html>
"""

LIST_ITEM = u"""    <li>
      <a href="posts/{slug}.html">
        <span class="date">{date_dots}</span>
        <span class="title">{title}</span>
        <span class="excerpt">{excerpt}</span>
      </a>
    </li>
"""


def esc(s):
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def date_dots(date_str):
    parts = re.split(r"[-/.]", date_str.strip())
    if len(parts) == 3:
        return "%s · %s · %s" % (parts[0], parts[1].zfill(2), parts[2].zfill(2))
    return date_str


def write_article(payload):
    title = (payload.get("title") or "").strip()
    slug = (payload.get("slug") or "").strip()
    excerpt = (payload.get("excerpt") or "").strip()
    body = payload.get("body") or ""
    date_str = (payload.get("date") or "").strip()
    minutes = payload.get("minutes") or 3

    if not title:
        raise ValueError("标题不能为空")
    if not re.match(r"^[a-z0-9][a-z0-9-]*$", slug):
        raise ValueError("短名只能用小写字母、数字和短横线")
    if not body.strip():
        raise ValueError("正文不能为空")

    dd = date_dots(date_str)
    html = ARTICLE.format(
        title=esc(title), excerpt=esc(excerpt or title), date_dots=esc(dd),
        minutes=minutes, body=body,
    )

    posts_dir = os.path.join(ROOT, "posts")
    os.makedirs(posts_dir, exist_ok=True)
    path = os.path.join(posts_dir, slug + ".html")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)

    update_index(title, slug, dd, excerpt or title)
    return path


def update_index(title, slug, dd, excerpt):
    idx_path = os.path.join(ROOT, "index.html")
    with open(idx_path, "r", encoding="utf-8") as f:
        html = f.read()

    # 已存在同名文章：先移除旧条目，避免重复
    pattern = re.compile(
        r"\n?[ \t]*<li>\s*<a href=\"posts/" + re.escape(slug) + r"\.html\">.*?</li>",
        re.S,
    )
    html = pattern.sub("", html)

    item = LIST_ITEM.format(
        slug=slug, date_dots=esc(dd), title=esc(title), excerpt=esc(excerpt)
    )
    marker = '<ul class="post-list">'
    pos = html.find(marker)
    if pos < 0:
        raise RuntimeError("index.html 里找不到文章列表容器")
    insert_at = pos + len(marker)
    # 跳过紧跟的空白
    while insert_at < len(html) and html[insert_at] in " \t\r\n":
        insert_at += 1
    html = html[:insert_at] + "\n" + item + html[insert_at:]

    with open(idx_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)


def publish(payload):
    write_article(payload)
    slug = payload["slug"].strip()
    ok_c, msg_c = git_commit("post: " + payload["title"].strip())
    if not ok_c:
        return {"ok": False, "stage": "commit", "error": msg_c}
    ok_p, via, detail = git_push()
    if not ok_p:
        return {"ok": False, "stage": "push", "error": detail, "committed": True}
    return {
        "ok": True,
        "url": "%s/posts/%s.html" % (SITE, slug),
        "home": SITE + "/",
        "via": via,
        "commit": msg_c,
    }


# ---------------------------------------------------------------- HTTP 服务

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "xiray-writer/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html", "/writer.html"):
            fp = os.path.join(TOOLS, "writer.html")
            if not os.path.exists(fp):
                return self._send(500, "找不到 writer.html", "text/plain; charset=utf-8")
            with open(fp, "rb") as f:
                return self._send(200, f.read(), CONTENT_TYPES[".html"])
        if path == "/api/info":
            return self._json({"root": ROOT, "site": SITE, "port": PORT})
        return self._serve_static(path)

    def _serve_static(self, path):
        rel = path.lstrip("/")
        if not (rel.startswith("assets/") or rel.startswith("posts/")):
            return self._send(404, "not found", "text/plain; charset=utf-8")
        root_abs = os.path.normpath(ROOT)
        fp = os.path.normpath(os.path.join(root_abs, rel.replace("/", os.sep)))
        if not fp.startswith(root_abs + os.sep):
            return self._send(403, "forbidden", "text/plain; charset=utf-8")
        if not os.path.isfile(fp):
            return self._send(404, "not found", "text/plain; charset=utf-8")
        ext = os.path.splitext(fp)[1].lower()
        ctype = CONTENT_TYPES.get(ext, "application/octet-stream")
        with open(fp, "rb") as f:
            return self._send(200, f.read(), ctype)

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception as e:
            return self._json({"ok": False, "error": "请求格式错误: %s" % e}, 400)

        if path == "/api/publish":
            try:
                return self._json(publish(payload))
            except ValueError as e:
                return self._json({"ok": False, "stage": "check", "error": str(e)}, 200)
            except Exception as e:
                return self._json({"ok": False, "stage": "write", "error": repr(e)}, 200)

        if path == "/api/proxy":
            p = find_proxy(force=True)
            return self._json({"ok": True, "proxy": p or "直连"})

        return self._json({"ok": False, "error": "未知接口"}, 404)


def serve():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("")
    print("  xiray 写作台已启动")
    print("  地址: http://127.0.0.1:%d" % PORT)
    print("  目录: %s" % ROOT)
    print("  关闭: 直接关掉这个窗口")
    print("")
    if webbrowser:
        threading.Timer(0.6, lambda: webbrowser.open("http://127.0.0.1:%d" % PORT)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def main():
    if "--sync" in sys.argv:
        print("探测代理...")
        p = find_proxy(force=True)
        print("代理: %s" % (p or "直连"))
        ok, msg = git_commit("update: 手动更新")
        print("提交: %s" % msg)
        if not ok:
            sys.exit(1)
        ok, via, detail = git_push()
        if ok:
            print("推送成功（%s）" % via)
            print("站点: %s" % SITE)
        else:
            print("推送失败: %s" % detail)
            sys.exit(1)
        return
    serve()


if __name__ == "__main__":
    main()
