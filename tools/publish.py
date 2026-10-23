#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
xiray 博客写作台 —— 本地服务

能力：
  · 写作界面（富文本 / Markdown 双模式，可互转）
  · 发布 / 编辑已有文章 / 删除文章
  · 类别管理（可自定义新增）
  · 附件上传
  · 自动维护首页列表、搜索索引
  · 代理自动探测后提交推送

用法：
  双击 写作台.bat           启动服务并打开浏览器
  python publish.py --sync    只做「提交+推送」
  python publish.py --rebuild 重建首页列表与搜索索引
"""

import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import parse_qs

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
SOURCES = os.path.join(TOOLS, "sources")
POSTS = os.path.join(ROOT, "posts")
FILES = os.path.join(ROOT, "files")
CATS_FILE = os.path.join(ROOT, "categories.json")
SEARCH_FILE = os.path.join(ROOT, "search-index.json")
PORT = 5178
SITE = "https://xiray.github.io/blog"

DEFAULT_CATS = ["代码编写", "Agent 编程", "折腾记录", "游戏", "前端"]

# ================================================================ 代理探测

COMMON_PORTS = [7897, 7890, 7891, 10809, 10808, 1080, 8889, 64836, 33210, 2080, 4780]
_proxy_cache = {"value": "unknown", "at": 0.0}


def _normalize(p):
    p = (p or "").strip()
    if not p:
        return None
    if "://" not in p:
        p = "http://" + p
    return p


def proxy_candidates():
    found = []

    def add(p):
        p = _normalize(p)
        if p and p not in found:
            found.append(p)

    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
                "ALL_PROXY", "all_proxy"):
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


def _probe(url, proxy=None, timeout=6):
    try:
        if proxy:
            handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        else:
            handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(handler)
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "xiray-blog-publish")
        with opener.open(req, timeout=timeout) as resp:
            return resp.status < 400
    except Exception:
        return False


def find_proxy(force=False):
    now = time.time()
    if not force and _proxy_cache["value"] != "unknown" and now - _proxy_cache["at"] < 120:
        return _proxy_cache["value"]

    cands = proxy_candidates()
    verdict = {}

    def work(p):
        verdict[p] = _probe("https://github.com/login", p)

    threads = [threading.Thread(target=work, args=(p,), daemon=True) for p in cands]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=8)

    picked = None
    for p in cands:
        if verdict.get(p):
            picked = p
            break
    if picked is None and _probe("https://github.com/login"):
        picked = None  # 直连可用

    _proxy_cache["value"] = picked
    _proxy_cache["at"] = now
    return picked


# ================================================================ git

def run_git(args, timeout=120):
    try:
        r = subprocess.run(["git", "-C", ROOT] + args, capture_output=True, timeout=timeout)
        return (r.returncode,
                (r.stdout or b"").decode("utf-8", "replace"),
                (r.stderr or b"").decode("utf-8", "replace"))
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


def sync_repo(message):
    ok, msg = git_commit(message)
    if not ok:
        return {"ok": False, "stage": "commit", "error": msg}
    ok, via, detail = git_push()
    if not ok:
        return {"ok": False, "stage": "push", "error": detail, "committed": True}
    return {"ok": True, "via": via, "commit": msg}


# ================================================================ 基础工具

def ensure_dirs():
    for d in (SOURCES, POSTS, FILES, TOOLS):
        os.makedirs(d, exist_ok=True)


def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_categories():
    data = read_json(CATS_FILE, None)
    if not isinstance(data, dict) or "categories" not in data:
        data = {"categories": list(DEFAULT_CATS)}
        write_json(CATS_FILE, data)
    return data["categories"]


def save_categories(cats):
    write_json(CATS_FILE, {"categories": cats})


def src_path(slug):
    return os.path.join(SOURCES, slug + ".json")


def read_source(slug):
    return read_json(src_path(slug), None)


def write_source(slug, data):
    write_json(src_path(slug), data)


def esc(s):
    return (str(s or "")
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def strip_html(html):
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html or "", flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                 ("&gt;", ">"), ("&quot;", '"')):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def date_dots(date_str):
    parts = re.split(r"[-/.]", (date_str or "").strip())
    if len(parts) == 3:
        return "%s · %s · %s" % (parts[0], parts[1].zfill(2), parts[2].zfill(2))
    return date_str or ""


# ================================================================ 文章模板

ARTICLE = u"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — xiray</title>
<meta name="description" content="{excerpt}">
<meta name="category" content="{category}">
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
      <p class="article-meta">{date_dots}
        <span class="dot">·</span> <span class="cat-chip">{category}</span>
        <span class="dot">·</span> 约 {minutes} 分钟</p>
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

LIST_ITEM = u"""    <li data-slug="{slug}" data-date="{date}" data-category="{category}">
      <a href="posts/{slug}.html">
        <span class="date">{date_dots}</span>
        <span class="title">{title}</span>
        <span class="excerpt">{excerpt}</span>
      </a>
      <span class="post-cat">{category}</span>
    </li>"""


def build_article(meta, body_html):
    return ARTICLE.format(
        title=esc(meta["title"]),
        excerpt=esc(meta.get("excerpt") or meta["title"]),
        category=esc(meta.get("category") or "未分类"),
        date_dots=esc(date_dots(meta.get("date"))),
        minutes=meta.get("minutes") or 3,
        body=body_html,
    )


# ================================================================ 首页列表 / 索引

def list_posts():
    out = []
    if not os.path.isdir(SOURCES):
        return out
    for fn in os.listdir(SOURCES):
        if not fn.endswith(".json"):
            continue
        d = read_json(os.path.join(SOURCES, fn), None)
        if not d or not d.get("slug"):
            continue
        out.append({
            "slug": d["slug"],
            "title": d.get("title") or d["slug"],
            "date": d.get("date") or "",
            "category": d.get("category") or "未分类",
            "excerpt": d.get("excerpt") or "",
            "mode": d.get("mode") or "rich",
            "updated": d.get("updated") or "",
        })
    out.sort(key=lambda x: (x["date"], x["updated"]), reverse=True)
    return out


def rebuild_index_list():
    """按 sources 重建 index.html 的列表（唯一真源 = tools/sources/*.json）"""
    posts = list_posts()
    items = [LIST_ITEM.format(
        slug=p["slug"],
        date=esc(p["date"]),
        date_dots=esc(date_dots(p["date"])),
        category=esc(p.get("category") or "未分类"),
        title=esc(p["title"]),
        excerpt=esc(p.get("excerpt") or p["title"]),
    ) for p in posts]

    idx_path = os.path.join(ROOT, "index.html")
    with open(idx_path, "r", encoding="utf-8") as f:
        html = f.read()

    block = ("\n\n" + "\n\n".join(items) + "\n\n  ") if items else "\n\n  "
    pattern = re.compile(r'(<ul class="post-list"[^>]*>).*?(</ul>)', re.S)
    if not pattern.search(html):
        raise RuntimeError("index.html 里找不到文章列表容器")
    html = pattern.sub(lambda m: m.group(1) + block + m.group(2), html, count=1)

    with open(idx_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)


def rebuild_search_index():
    items = []
    for p in list_posts():
        src = read_source(p["slug"]) or {}
        items.append({
            "slug": p["slug"],
            "title": p["title"],
            "date": p["date"],
            "category": p.get("category") or "未分类",
            "excerpt": p.get("excerpt") or "",
            "text": strip_html(src.get("body_html") or "")[:4000],
        })
    write_json(SEARCH_FILE, {"updated": time.strftime("%Y-%m-%d %H:%M"),
                             "items": items})


# ================================================================ 发布 / 删除

def publish(payload):
    title = (payload.get("title") or "").strip()
    slug = (payload.get("slug") or "").strip()
    body_html = payload.get("body_html") or ""
    source = payload.get("source") or ""
    mode = payload.get("mode") or "rich"
    category = (payload.get("category") or "").strip() or "未分类"
    date_str = (payload.get("date") or "").strip()

    if not title:
        raise ValueError("标题不能为空")
    if not re.match(r"^[a-z0-9][a-z0-9-]*$", slug):
        raise ValueError("短名只能用小写字母、数字和短横线")
    if not strip_html(body_html):
        raise ValueError("正文是空的")

    existed = os.path.exists(src_path(slug))

    meta = {
        "slug": slug,
        "title": title,
        "date": date_str or time.strftime("%Y-%m-%d"),
        "category": category,
        "excerpt": (payload.get("excerpt") or "").strip() or title,
        "minutes": payload.get("minutes") or 3,
        "mode": mode,
        "source": source,
        "body_html": body_html,
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    write_source(slug, meta)

    with open(os.path.join(POSTS, slug + ".html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(build_article(meta, body_html))

    rebuild_index_list()
    rebuild_search_index()

    res = sync_repo(("update: " if existed else "post: ") + title)
    if not res.get("ok"):
        return res
    res.update({"url": "%s/posts/%s.html" % (SITE, slug),
                "home": SITE + "/", "slug": slug, "existed": existed})
    return res


def delete_post(slug):
    if not re.match(r"^[a-z0-9][a-z0-9-]*$", slug or ""):
        raise ValueError("短名不合法")
    src = read_source(slug)
    if not src:
        raise ValueError("找不到这篇文章")

    for p in (src_path(slug), os.path.join(POSTS, slug + ".html")):
        if os.path.exists(p):
            os.remove(p)

    rebuild_index_list()
    rebuild_search_index()

    res = sync_repo("delete: " + (src.get("title") or slug))
    res["slug"] = slug
    return res


# ================================================================ 附件

SAFE_NAME = re.compile(r"[^A-Za-z0-9._\u4e00-\u9fa5-]+")


def save_upload(filename, b64data):
    if not b64data:
        raise ValueError("附件内容为空")
    if "," in b64data and b64data.strip().startswith("data:"):
        b64data = b64data.split(",", 1)[1]
    raw = base64.b64decode(b64data)
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError("附件超过 25MB")

    base = os.path.basename(filename or "file")
    stem, ext = os.path.splitext(base)
    stem = SAFE_NAME.sub("-", stem)[:60] or "file"
    name = "%s-%s%s" % (time.strftime("%Y%m%d%H%M%S"), stem, ext.lower())

    os.makedirs(FILES, exist_ok=True)
    with open(os.path.join(FILES, name), "wb") as f:
        f.write(raw)

    size = len(raw)
    if size < 1024:
        human = "%d B" % size
    elif size < 1048576:
        human = "%.0f KB" % (size / 1024.0)
    else:
        human = "%.1f MB" % (size / 1048576.0)

    return {"name": name, "display": base, "size": human,
            "url": "../files/" + name, "bytes": size}


# ================================================================ HTTP

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".ico": "image/x-icon",
    ".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8",
    ".zip": "application/zip", ".md": "text/plain; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "xiray-writer/2.0"

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
        path, _, qs = self.path.partition("?")
        query = {k: v[0] for k, v in parse_qs(qs).items()}

        if path in ("/", "/writer.html"):
            fp = os.path.join(TOOLS, "writer.html")
            if not os.path.exists(fp):
                return self._send(500, "找不到 writer.html", "text/plain; charset=utf-8")
            with open(fp, "rb") as f:
                return self._send(200, f.read(), CONTENT_TYPES[".html"])

        if path == "/api/info":
            return self._json({"root": ROOT, "site": SITE, "port": PORT})

        if path == "/api/posts":
            return self._json({"ok": True, "posts": list_posts()})

        if path == "/api/post":
            data = read_source(query.get("slug", ""))
            if not data:
                return self._json({"ok": False, "error": "找不到这篇文章"}, 404)
            return self._json({"ok": True, "post": data})

        if path == "/api/categories":
            return self._json({"ok": True, "categories": load_categories()})

        return self._serve_static(path)

    def _serve_static(self, path):
        rel = path.lstrip("/")
        allowed_prefix = ("assets/", "posts/", "files/")
        allowed_file = ("search-index.json", "categories.json",
                        "index.html", "about.html")
        if not (rel.startswith(allowed_prefix) or rel in allowed_file):
            return self._send(404, "not found", "text/plain; charset=utf-8")
        root_abs = os.path.normpath(ROOT)
        fp = os.path.normpath(os.path.join(root_abs, rel.replace("/", os.sep)))
        if not fp.startswith(root_abs + os.sep):
            return self._send(403, "forbidden", "text/plain; charset=utf-8")
        if not os.path.isfile(fp):
            return self._send(404, "not found", "text/plain; charset=utf-8")
        ext = os.path.splitext(fp)[1].lower()
        with open(fp, "rb") as f:
            return self._send(200, f.read(), CONTENT_TYPES.get(ext, "application/octet-stream"))

    def do_POST(self):
        path = self.path.partition("?")[0]
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception as e:
            return self._json({"ok": False, "error": "请求格式错误: %s" % e}, 400)

        try:
            if path == "/api/publish":
                return self._json(publish(payload))
            if path == "/api/delete":
                return self._json(delete_post(payload.get("slug", "")))
            if path == "/api/upload":
                return self._json({"ok": True,
                                   "file": save_upload(payload.get("filename"),
                                                       payload.get("data"))})
            if path == "/api/categories":
                return self._json(self._handle_categories(payload))
            if path == "/api/proxy":
                return self._json({"ok": True, "proxy": find_proxy(force=True) or "直连"})
        except ValueError as e:
            return self._json({"ok": False, "stage": "check", "error": str(e)})
        except Exception as e:
            return self._json({"ok": False, "stage": "server", "error": repr(e)})

        return self._json({"ok": False, "error": "未知接口"}, 404)

    def _handle_categories(self, payload):
        cats = load_categories()
        action = payload.get("action") or "add"
        name = (payload.get("name") or "").strip()

        if action == "add":
            if not name:
                raise ValueError("类别名不能为空")
            if name not in cats:
                cats.append(name)
                save_categories(cats)
            return {"ok": True, "categories": cats}

        if action == "remove":
            used = [p for p in list_posts() if p.get("category") == name]
            if used:
                raise ValueError("还有 %d 篇文章在用「%s」，先改掉再删" % (len(used), name))
            if name in cats:
                cats.remove(name)
                save_categories(cats)
            return {"ok": True, "categories": cats}

        raise ValueError("未知操作")


def serve():
    ensure_dirs()
    load_categories()
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
        p = find_proxy(force=True)
        print("代理: %s" % (p or "直连"))
        res = sync_repo("update: 手动更新")
        if res.get("ok"):
            print("推送成功（%s）" % res.get("via"))
            print("站点: %s" % SITE)
        else:
            print("失败: %s" % res.get("error"))
            sys.exit(1)
        return

    if "--rebuild" in sys.argv:
        ensure_dirs()
        load_categories()
        rebuild_index_list()
        rebuild_search_index()
        print("已重建首页列表与搜索索引")
        return

    serve()


if __name__ == "__main__":
    main()
