#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
xiray 博客写作台 —— 本地服务

能力：
  · 写作界面（富文本 / Markdown 双模式，可互转）
  · 发布 / 编辑已有文章 / 删除文章
  · 类别管理（可自定义新增）
  · 附件上传
  · AI 问答（接 DeepSeek 等 OpenAI 兼容接口，流式输出）
  · 自动维护首页列表、搜索索引
  · 代理自动探测后提交推送

用法：
  双击 写作台.bat           启动服务并打开浏览器
  python publish.py --sync    只做「提交+推送」
  python publish.py --rebuild 重建首页列表与搜索索引
"""

import base64
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit

try:
    import winreg
except ImportError:
    winreg = None

try:
    import webbrowser
except ImportError:
    webbrowser = None

# 图片压缩用 Pillow。没装就跳过压缩，不影响其它功能。
try:
    from PIL import Image, ImageOps
    _RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS
except Exception:
    Image = None
    ImageOps = None
    _RESAMPLE = None

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

# 图片压缩参数
IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp")
IMG_MAX_SIDE = 1600          # 长边上限（px）
IMG_QUALITY = 82             # JPEG / WebP 质量
IMG_MIN_BYTES = 120 * 1024   # 小于这个体积的图不值得重压

# 搜索索引里每篇保留的正文字数（用于全文搜索，不是全文备份）
SEARCH_TEXT_LIMIT = 2500

# ================================================================ 发布进度
# 服务端在发布过程中更新这里，前端轮询 /api/progress 显示当前卡在哪一步。

_PROGRESS = {"stage": "", "detail": "", "at": 0.0}
_PROGRESS_LOCK = threading.Lock()


def set_progress(stage, detail=""):
    with _PROGRESS_LOCK:
        _PROGRESS["stage"] = stage
        _PROGRESS["detail"] = detail
        _PROGRESS["at"] = time.time()


def get_progress():
    with _PROGRESS_LOCK:
        return dict(_PROGRESS)


def clear_progress():
    set_progress("", "")


# ================================================================ 代理
# 只认两种状态：用固定代理（默认 ray 一直在用的 7897）或者不用代理。
# 以前会在十几个常见端口上乱探，探到谁算谁，结果经常挑到一个半死不活的端口，
# 推送卡住、AI 报 502，看着像网络问题其实是选错了代理。

PROXY_FILE = os.path.join(TOOLS, ".proxy.json")
DEFAULT_PROXY = "http://127.0.0.1:7897"


def _normalize(p):
    p = (p or "").strip()
    if not p:
        return None
    if "://" not in p:
        p = "http://" + p
    return p


def load_proxy_mode():
    raw = read_json(PROXY_FILE, None)
    if isinstance(raw, dict) and raw.get("mode") in ("proxy", "off"):
        return {"mode": raw["mode"], "url": _normalize(raw.get("url")) or DEFAULT_PROXY}
    return {"mode": "proxy", "url": DEFAULT_PROXY}


def save_proxy_mode(payload):
    cur = load_proxy_mode()
    mode = "off" if (payload.get("mode") or "").strip() == "off" else "proxy"
    url = _normalize(payload.get("url")) or cur["url"] or DEFAULT_PROXY
    if not re.match(r"^https?://", url):
        raise ValueError("代理地址要以 http:// 开头，比如 http://127.0.0.1:7897")
    data = {"mode": mode, "url": url, "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
    write_json(PROXY_FILE, data)
    _proxy_cache.update({"at": 0.0, "ok": None})
    return data


def proxy_url():
    """当前该用的代理；返回 None 表示直连。"""
    m = load_proxy_mode()
    return m["url"] if m["mode"] == "proxy" else None


_proxy_cache = {"at": 0.0, "ok": None}


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


def proxy_status(force=False):
    """界面上的小状态：当前用的是哪个代理、通不通。"""
    m = load_proxy_mode()
    now = time.time()
    if not force and _proxy_cache["ok"] is not None and now - _proxy_cache["at"] < 60:
        ok = _proxy_cache["ok"]
    else:
        ok = _probe("https://github.com/login", proxy_url(), timeout=5)
        _proxy_cache.update({"at": now, "ok": ok})
    m["ok"] = ok
    m["effective"] = m["url"] if m["mode"] == "proxy" else "直连"
    return m


def find_proxy(force=False):
    """兼容旧调用点：返回可用代理地址，None = 直连。"""
    url = proxy_url()
    if not url:
        return None
    if force or _proxy_cache["ok"] is None:
        proxy_status(force=True)
    return url if _proxy_cache["ok"] is not False else None


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


# 提交时只跟踪这些路径。用 git add -A 会把工作区里任何临时文件一起提交，
# 之前就发生过测试页被发布流程顺手带进仓库的情况。
TRACK_PATHS = ["index.html", "about.html", "README.md", "categories.json",
               "search-index.json", "assets", "posts", "files", "tools",
               ".gitignore", ".nojekyll"]


def git_commit(message):
    run_git(["add", "-A", "--"] + TRACK_PATHS)
    code, out, err = run_git(["commit", "-m", message])
    if code == 0:
        return True, "已提交"
    blob = (out + err).lower()
    if "nothing to commit" in blob or "no changes added" in blob:
        return True, "内容无变化"
    return False, (out + err).strip()[:400]


def git_push():
    """推送。用配置里的代理（默认 7897）试三次，最后一次改成直连兜底。

    以前每次尝试都会重新探测代理，探测本身又要几秒，还会把「其实直连就行」
    的情况拖成半分钟。现在代理地址是定死的，失败只降级到直连。
    """
    proxy = proxy_url()
    plans = [proxy, proxy, None] if proxy else [None]
    last = ""
    for i, p in enumerate(plans):
        set_progress("推送", "第 %d 次尝试%s" % (
            i + 1, ("（代理 " + p.replace("http://", "") + "）") if p else "（直连）"))
        cfg = ["-c", "http.proxy=" + (p or ""), "-c", "https.proxy=" + (p or "")]
        code, out, err = run_git(cfg + ["push"], timeout=150)
        if code == 0:
            return True, ("经由 " + p) if p else "直连", out.strip()
        last = (err or out).strip()
        time.sleep(1)
    return False, "", last[:500]


def sync_repo(message):
    set_progress("提交", "git add / commit")
    ok, msg = git_commit(message)
    if not ok:
        return {"ok": False, "stage": "commit", "error": msg}
    ok, via, detail = git_push()
    if not ok:
        return {"ok": False, "stage": "push", "error": detail, "committed": True}
    return {"ok": True, "via": via, "commit": msg}


def local_changes():
    """本地还没同步到 GitHub 的东西：未提交的文件 + 已提交但没推上去的提交。

    删文章不再每删一篇就 commit+push（一次删除要好几十秒，还容易撞上代理不稳），
    改成先在本地删掉、攒着，由「更新」按钮一次性推上去。这里就是攒了多少。
    """
    code, out, _ = run_git(["status", "--porcelain", "--"] + TRACK_PATHS)
    lines = []
    if code == 0:
        for raw in out.splitlines():
            raw = raw.rstrip()
            if len(raw) > 3:
                lines.append(raw)

    ahead = 0
    c2, out2, _ = run_git(["rev-list", "--count", "@{u}..HEAD"])
    if c2 == 0:
        try:
            ahead = int((out2 or "0").strip())
        except ValueError:
            ahead = 0

    return {
        "count": len(lines),
        "ahead": ahead,
        "files": [(l[3:] if len(l) > 3 else l).strip() for l in lines],
        "statuses": [l[:2] for l in lines],
    }


def sync_all(message=""):
    """「更新」按钮：把本地所有改动一次提交并推上去。"""
    ch = local_changes()
    if not ch["count"] and not ch["ahead"]:
        return {"ok": True, "noop": True, "count": 0, "ahead": 0,
                "message": "没有要更新的改动，本地和线上一致"}

    if not message:
        only_del = ch["count"] and all("D" in s for s in ch["statuses"])
        if only_del:
            message = "delete: 同步删掉的文章（%d 个文件）" % ch["count"]
        elif ch["count"]:
            message = "update: 同步本地改动（%d 个文件）" % ch["count"]
        else:
            message = "update: 推送本地提交"

    res = sync_repo(message)
    res.update({"count": ch["count"], "ahead": ch["ahead"], "files": ch["files"][:20]})
    return res


# ================================================================ 从 GitHub 拉取

def _git_cfg(proxy):
    return ["-c", "http.proxy=" + (proxy or ""), "-c", "https.proxy=" + (proxy or "")]


def _net_plans():
    """代理优先，直连兜底。跟推送用的是同一套代理配置。"""
    p = proxy_url()
    return [p, None] if p else [None]


def _rev_count(rev):
    code, out, _ = run_git(["rev-list", "--count", rev])
    try:
        return int((out or "0").strip())
    except ValueError:
        return 0


def git_fetch():
    last = ""
    for i, p in enumerate(_net_plans()):
        set_progress("拉取", "连接 GitHub 第 %d 次%s" % (
            i + 1, ("（代理 " + p.replace("http://", "") + "）") if p else "（直连）"))
        code, out, err = run_git(_git_cfg(p) + ["fetch", "origin", "--prune"], timeout=150)
        if code == 0:
            return True, p, (out + err).strip()
        last = (err or out).strip()
        time.sleep(1)
    return False, None, last[:400]


def git_pull():
    """「拉取」：把 GitHub 上的新内容同步到本地。

    只做快进（--ff-only），永远不覆盖本地改动：
    本地有没提交的文件、或有没推上去的提交，就直接拒绝并提示先点「更新」，
    免得一次 pull 把写了一半的东西盖掉、或者搞出冲突来。
    """
    ok, via, detail = git_fetch()
    if not ok:
        return {"ok": False, "stage": "fetch", "error": detail or "连不上 GitHub",
                "message": "连不上 GitHub，先看看代理是不是开着"}

    behind = _rev_count("HEAD..@{u}")
    ahead = _rev_count("@{u}..HEAD")
    if not behind:
        return {"ok": True, "noop": True, "behind": 0, "ahead": ahead, "via": via,
                "message": "已经是最新的，GitHub 上没有更新的内容"}

    ch = local_changes()
    if ch["count"]:
        return {"ok": False, "stage": "dirty", "behind": behind, "ahead": ahead,
                "files": ch["files"][:20],
                "message": "本地还有 %d 个文件没推上去，先点「更新」再拉取" % ch["count"]}
    if ahead:
        return {"ok": False, "stage": "diverged", "behind": behind, "ahead": ahead,
                "message": "本地有 %d 个提交还没推上去，先点「更新」" % ahead}

    old = (run_git(["rev-parse", "HEAD"])[1] or "").strip()
    set_progress("拉取", "快进 %d 个提交" % behind)
    code, out, err = run_git(["merge", "--ff-only", "@{u}"], timeout=120)
    if code != 0:
        return {"ok": False, "stage": "merge", "error": (out + err).strip()[:400],
                "message": "快进失败，可能有文件被别的程序占着"}
    new = (run_git(["rev-parse", "HEAD"])[1] or "").strip()
    _, names, _ = run_git(["diff", "--name-only", old, new])
    files = [f.strip() for f in (names or "").splitlines() if f.strip()]
    return {"ok": True, "behind": behind, "ahead": 0, "via": via,
            "from": old[:7], "to": new[:7], "count": len(files), "files": files[:40],
            "message": "已拉取 %d 个提交，%d 个文件有变化" % (behind, len(files))}


# ================================================================ 历史里被删掉的文章

def deleted_posts():
    """git 历史里删掉、磁盘上已经没有的文章，用来把误删的找回来。

    同一篇被删过多次的只留最近那一次；磁盘上还在的跳过。
    """
    code, out, _ = run_git(["log", "--diff-filter=D", "--name-only",
                            "--pretty=format:@@%h|%ad", "--date=short",
                            "--", "posts/"])
    if code != 0:
        return []

    cur, found = None, {}
    for raw in (out or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("@@"):
            h, _, d = line[2:].partition("|")
            cur = (h.strip(), d.strip())
            continue
        if not cur or not line.startswith("posts/") or not line.endswith(".html"):
            continue
        slug = os.path.basename(line)[:-5]
        if slug in found:
            continue
        if os.path.exists(os.path.join(POSTS, slug + ".html")):
            continue
        found[slug] = {"slug": slug, "commit": cur[0], "deleted": cur[1]}

    posts = []
    for slug, info in found.items():
        _, blob, _ = run_git(["show", "%s^:posts/%s.html" % (info["commit"], slug)])
        m = re.search(r"<h1[^>]*>(.*?)</h1>", blob or "", re.S)
        info["title"] = strip_html(m.group(1)) if m else slug
        posts.append(info)
    posts.sort(key=lambda x: (x["deleted"], x["slug"]), reverse=True)
    return posts


def restore_post(slug):
    """把历史里删掉的文章恢复回磁盘（html + 写作台的源 json）。"""
    if not re.match(r"^[a-z0-9][a-z0-9-]*$", slug or ""):
        raise ValueError("短名不合法")
    target = os.path.join(POSTS, slug + ".html")
    if os.path.exists(target):
        return {"ok": False, "error": "这篇文章还在，不用恢复"}

    item = next((p for p in deleted_posts() if p["slug"] == slug), None)
    if not item:
        return {"ok": False, "error": "git 历史里找不到这篇"}

    rev = "%s^:posts/%s.html" % (item["commit"], slug)
    code, blob, err = run_git(["show", rev])
    if code != 0 or not (blob or "").strip():
        return {"ok": False, "error": "取历史内容失败：%s" % (err or "")[:200]}

    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write(blob.replace("\r\n", "\n"))

    # 源码 json 也一起还原，否则写作台打开它是空的
    src = src_path(slug)
    if not os.path.exists(src):
        c2, sblob, _ = run_git(["show", "%s^:tools/sources/%s.json" % (item["commit"], slug)])
        if c2 == 0 and (sblob or "").strip():
            try:
                data = json.loads(sblob)
            except Exception:
                data = None
            if isinstance(data, dict):
                write_source(slug, data)

    set_progress("恢复", "重建首页列表与搜索索引")
    rebuild_index_list()
    rebuild_search_index()
    return {"ok": True, "slug": slug, "title": item.get("title"),
            "message": "已恢复《%s》，记得点「更新」推上线" % item.get("title")}


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

<script src="../assets/hl.js"></script>
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

YEAR_HEAD = u"""    <li class="year-head" data-year="{year}">
      <span class="year-label">{year}</span>
      <span class="year-count">{count} 篇</span>
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
    """按 sources 重建 index.html 的列表（唯一真源 = tools/sources/*.json）

    文章按年份分组：每年插一条 year-head 分隔行，列表本身仍是 flat <li>，
    这样首页筛选逻辑（找 li[data-slug]）不用改。
    """
    posts = list_posts()

    year_count = {}
    for p in posts:
        y = (p["date"] or "")[:4] or "未标日期"
        year_count[y] = year_count.get(y, 0) + 1

    items = []
    current_year = None
    for p in posts:
        year = (p["date"] or "")[:4] or "未标日期"
        if year != current_year:
            items.append(YEAR_HEAD.format(year=esc(year), count=year_count[year]))
            current_year = year
        items.append(LIST_ITEM.format(
            slug=p["slug"],
            date=esc(p["date"]),
            date_dots=esc(date_dots(p["date"])),
            category=esc(p.get("category") or "未分类"),
            title=esc(p["title"]),
            excerpt=esc(p.get("excerpt") or p["title"]),
        ))

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
        plain = strip_html(src.get("body_html") or "")
        items.append({
            "slug": p["slug"],
            "title": p["title"],
            "date": p["date"],
            "category": p.get("category") or "未分类",
            "excerpt": p.get("excerpt") or "",
            "text": plain[:SEARCH_TEXT_LIMIT],
        })
    data = {"updated": time.strftime("%Y-%m-%d %H:%M"), "items": items}
    # 索引用紧凑格式写：它只给浏览器读，不需要缩进。去掉缩进能省一半体积。
    tmp = SEARCH_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, SEARCH_FILE)


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

    set_progress("生成", "写入文章与首页列表")

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

    set_progress("生成", "重建首页列表与搜索索引")
    rebuild_index_list()
    rebuild_search_index()

    # 只删本地，不在这里推 GitHub：删三篇要等三次推送，还得赌代理。
    # 攒到点「更新」时一次性提交推送。
    ch = local_changes()
    return {"ok": True, "slug": slug, "deleted": True,
            "pending": ch["count"], "ahead": ch["ahead"],
            "message": "已从本地删掉，点「更新」才推到线上"}


# ================================================================ 附件

SAFE_NAME = re.compile(r"[^A-Za-z0-9._\u4e00-\u9fa5-]+")


def human_size(n):
    if n < 1024:
        return "%d B" % n
    if n < 1048576:
        return "%.0f KB" % (n / 1024.0)
    return "%.1f MB" % (n / 1048576.0)


def compress_image(raw, ext):
    """压缩图片。返回 (数据, 是否压缩过, 说明)。

    · 只处理 jpg/jpeg/png/webp；gif 不动（会丢帧），svg 不动
    · 长边超过 IMG_MAX_SIDE 才缩放
    · 压缩后反而更大就保留原图
    · 没装 Pillow 直接原样返回，不影响上传
    """
    if Image is None or ext not in IMG_EXTS:
        return raw, False, ""
    if len(raw) < IMG_MIN_BYTES:
        return raw, False, ""
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
        im = ImageOps.exif_transpose(im)   # 手机拍的竖图别躺倒

        w, h = im.size
        scale = min(1.0, float(IMG_MAX_SIDE) / max(w, h))
        if scale < 1.0:
            im = im.resize((max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
                           _RESAMPLE)

        out = io.BytesIO()
        if ext in (".jpg", ".jpeg"):
            im.convert("RGB").save(out, "JPEG", quality=IMG_QUALITY,
                                   optimize=True, progressive=True)
        elif ext == ".webp":
            im.save(out, "WEBP", quality=IMG_QUALITY, method=4)
        else:  # png：保留透明通道，只做尺寸缩放 + 无损优化
            im = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
            im.save(out, "PNG", optimize=True)

        data = out.getvalue()
        if len(data) >= len(raw) * 0.95:
            return raw, False, ""
        return data, True, "%dx%d" % im.size
    except Exception:
        return raw, False, ""


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

    data, compressed, note = compress_image(raw, ext.lower())

    os.makedirs(FILES, exist_ok=True)
    with open(os.path.join(FILES, name), "wb") as f:
        f.write(data)

    return {"name": name, "display": base,
            "size": human_size(len(data)), "url": "../files/" + name,
            "bytes": len(data), "orig_bytes": len(raw),
            "compressed": compressed, "note": note}


# ================================================================ AI 问答

AI_CONFIG = os.path.join(TOOLS, ".ai-config.json")
AI_DEFAULT_BASE = "https://api.deepseek.com"
AI_DEFAULT_MODEL = "deepseek-chat"
AI_MAX_MESSAGES = 40          # 只带最近这么多轮，避免请求无限膨胀
AI_CONNECT_TIMEOUT = 25
AI_READ_TIMEOUT = 180

AI_ERRORS = {
    400: "请求被拒绝（400），多半是模型名不对，去「设置」里点「拉取模型列表」看看有哪些",
    401: "API Key 无效（401），回「设置」里重新填一次",
    402: "账户余额不足（402），去 platform.deepseek.com 充值",
    403: "没有权限访问这个模型（403）",
    404: "接口地址不对（404），检查 Base URL 是不是被改错了",
    422: "参数不被接受（422），可能是模型不支持这次请求的格式",
    429: "请求太频繁或额度用完了（429），等一会儿再试",
    500: "服务端出错了（500），过一会儿再试",
    502: "网关错误（502）：代理没连上目标地址，检查 Base URL 或关掉代理再试",
    503: "服务暂时不可用（503），过一会儿再试",
    504: "网关超时（504）：代理在等上游响应时超时了",
}


def load_ai_config():
    raw = read_json(AI_CONFIG, {}) or {}
    if not isinstance(raw, dict):
        raw = {}
    base = (raw.get("base_url") or AI_DEFAULT_BASE).strip().rstrip("/")
    model = (raw.get("model") or AI_DEFAULT_MODEL).strip()
    key = (raw.get("api_key") or "").strip()
    source = "file"
    if not key:
        key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
        source = "env" if key else ""
    return {"base_url": base, "model": model, "api_key": key, "key_source": source}


def mask_key(k):
    if not k:
        return ""
    if len(k) <= 8:
        return "****"
    return k[:3] + "****" + k[-4:]


def public_ai_config(cfg=None):
    cfg = cfg or load_ai_config()
    return {
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "has_key": bool(cfg["api_key"]),
        "key_hint": mask_key(cfg["api_key"]),
        "key_source": cfg["key_source"],
        "config_path": os.path.relpath(AI_CONFIG, ROOT).replace(os.sep, "/"),
    }


def save_ai_config(payload):
    cfg = load_ai_config()
    base = (payload.get("base_url") or cfg["base_url"]).strip().rstrip("/")
    if not re.match(r"^https?://", base):
        raise ValueError("Base URL 必须以 http:// 或 https:// 开头")
    model = (payload.get("model") or cfg["model"]).strip()
    if not model:
        raise ValueError("模型名不能为空")

    key = payload.get("api_key")
    if key is None or key == "":
        key = cfg["api_key"]          # 留空表示不改
    elif key.strip() == "__clear__":
        key = ""
    else:
        key = key.strip()

    data = {"base_url": base, "model": model, "api_key": key,
            "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
    write_json(AI_CONFIG, data)
    try:
        os.chmod(AI_CONFIG, 0o600)
    except Exception:
        pass
    return public_ai_config(load_ai_config())


def _is_local_url(u):
    try:
        host = urlsplit(u).hostname or ""
    except Exception:
        return False
    return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or host.endswith(".local")


def ai_routes(cfg):
    """(标签, ProxyHandler)。先直连——API 大多在国内，绕代理反而更容易 502；
    直连不通再试设置里那个代理（跟 git push 用的是同一个）。"""
    if _is_local_url(cfg["base_url"]):
        # 本机地址走代理毫无意义，只会把「连接被拒」变成看不懂的 502
        yield "直连", urllib.request.ProxyHandler({})
        return

    yield "直连", urllib.request.ProxyHandler({})
    p = proxy_url()
    if p:
        yield "代理 " + p.replace("http://", ""), \
            urllib.request.ProxyHandler({"http": p, "https": p})


def ai_connect(cfg, path, data=None, timeout=None, accept="application/json"):
    """连上游。返回 (路由标签, response)。HTTP 状态错误会立刻抛出，不换路由重试。"""
    if not cfg["api_key"]:
        raise AIError("还没配置 API Key，点右上角「设置」填一个", 0, "")

    url = cfg["base_url"] + path
    timeout = timeout or (AI_CONNECT_TIMEOUT + AI_READ_TIMEOUT)
    errors = []

    for label, handler in ai_routes(cfg):
        opener = urllib.request.build_opener(handler)
        req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        req.add_header("Authorization", "Bearer " + cfg["api_key"])
        req.add_header("Accept", accept if not data else "application/json")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "xiray-writer-ai/1.0")
        try:
            return label, opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:400]
            except Exception:
                pass
            msg = AI_ERRORS.get(e.code) or ("接口返回 %d" % e.code)
            if label != "直连":
                msg += "（经由 %s）" % label
            if detail:
                msg += "\n上游原话：" + _ai_detail(detail)
            raise AIError(msg, e.code, label)
        except Exception as e:
            errors.append("%s：%s" % (label, e))
            continue

    raise AIError("连不上 %s\n%s" % (cfg["base_url"], " / ".join(errors)), 0, "")


def _ai_detail(raw):
    try:
        obj = json.loads(raw)
        err = obj.get("error")
        if isinstance(err, dict):
            return err.get("message") or raw
        if isinstance(err, str):
            return err
        return obj.get("message") or raw
    except Exception:
        return raw.strip()


class AIError(Exception):
    def __init__(self, msg, code=0, route=""):
        Exception.__init__(self, msg)
        self.msg = msg
        self.code = code
        self.route = route


def ai_list_models(cfg):
    _, resp = ai_connect(cfg, "/models", timeout=25)
    with resp:
        obj = json.loads(resp.read().decode("utf-8", "replace"))
    items = obj.get("data") if isinstance(obj, dict) else None
    if not isinstance(items, list):
        raise AIError("这个接口没有返回模型列表，手动填模型名就行", 0, "")
    ids = []
    for it in items:
        mid = it.get("id") if isinstance(it, dict) else None
        if mid and mid not in ids:
            ids.append(mid)
    return ids


def ai_chat(handler, payload):
    """流式转发上游的 SSE 给浏览器。每个事件都是一行 `data: {json}`。"""
    cfg = load_ai_config()
    if not cfg["api_key"]:
        return handler._json({"ok": False, "need_config": True,
                              "error": "还没配置 API Key，点右上角「设置」填一个"})

    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return handler._json({"ok": False, "error": "没有对话内容"}, 400)

    # 系统提示（引用的文章正文就放在这儿）必须留着，不能被轮数上限切掉；
    # 只对 user / assistant 做「最近 N 条」的限制。
    systems, rest = [], []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role not in ("user", "assistant", "system"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        (systems if role == "system" else rest).append({"role": role, "content": content})
    clean = systems + rest[-AI_MAX_MESSAGES:]
    if not clean:
        return handler._json({"ok": False, "error": "没有可发送的内容"}, 400)

    model = (payload.get("model") or cfg["model"]).strip()
    body = json.dumps({"model": model, "messages": clean, "stream": True},
                      ensure_ascii=False).encode("utf-8")

    try:
        route, resp = ai_connect(cfg, "/chat/completions", data=body,
                                 timeout=AI_CONNECT_TIMEOUT + AI_READ_TIMEOUT,
                                 accept="text/event-stream")
    except AIError as e:
        return handler._json({"ok": False, "error": e.msg, "code": e.code})

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "close")
    handler.send_header("X-Accel-Buffering", "no")
    handler.end_headers()

    def emit(obj):
        handler.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n")
                            .encode("utf-8"))
        handler.wfile.flush()

    emit({"type": "meta", "model": model, "route": route})

    reason_open = False
    try:
        with resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                except Exception:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                think = delta.get("reasoning_content")
                if think:
                    reason_open = True
                    emit({"type": "reason", "text": think})
                text = delta.get("content")
                if text:
                    if reason_open:
                        reason_open = False
                        emit({"type": "reason_end"})
                    emit({"type": "delta", "text": text})
        emit({"type": "done"})
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
        return                                  # 浏览器点了停止，正常退出
    except Exception as e:
        try:
            emit({"type": "error", "error": "读取上游数据时出错：%s" % e})
        except Exception:
            pass


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

        if path in ("/ai", "/ai.html"):
            fp = os.path.join(TOOLS, "ai.html")
            if not os.path.exists(fp):
                return self._send(500, "找不到 ai.html", "text/plain; charset=utf-8")
            with open(fp, "rb") as f:
                return self._send(200, f.read(), CONTENT_TYPES[".html"])

        if path == "/api/ai/config":
            return self._json({"ok": True, "config": public_ai_config()})

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

        if path == "/api/progress":
            return self._json({"ok": True, "progress": get_progress()})

        if path == "/api/proxy":
            return self._json({"ok": True, "status": proxy_status()})

        if path == "/api/sync":
            return self._json({"ok": True, "changes": local_changes()})

        if path == "/api/deleted":
            return self._json({"ok": True, "posts": deleted_posts()})

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
            if path == "/api/sync":
                return self._json(sync_all(payload.get("message") or ""))
            if path == "/api/pull":
                return self._json(git_pull())
            if path == "/api/restore":
                return self._json(restore_post(payload.get("slug", "")))
            if path == "/api/upload":
                return self._json({"ok": True,
                                   "file": save_upload(payload.get("filename"),
                                                       payload.get("data"))})
            if path == "/api/categories":
                return self._json(self._handle_categories(payload))
            if path == "/api/proxy":
                return self._json({"ok": True,
                                   "config": save_proxy_mode(payload),
                                   "status": proxy_status(force=True)})
            if path == "/api/ai/config":
                return self._json({"ok": True, "config": save_ai_config(payload)})
            if path == "/api/ai/models":
                return self._json(self._handle_ai_models(payload))
            if path == "/api/ai/chat":
                return ai_chat(self, payload)
        except AIError as e:
            return self._json({"ok": False, "error": e.msg, "code": e.code})
        except ValueError as e:
            return self._json({"ok": False, "stage": "check", "error": str(e)})
        except Exception as e:
            return self._json({"ok": False, "stage": "server", "error": repr(e)})

        return self._json({"ok": False, "error": "未知接口"}, 404)

    def _handle_ai_models(self, payload):
        cfg = load_ai_config()
        # 允许用表单里的值先试，不用先存
        base = (payload.get("base_url") or "").strip().rstrip("/")
        key = (payload.get("api_key") or "").strip()
        if base:
            cfg["base_url"] = base
        if key:
            cfg["api_key"] = key
        return {"ok": True, "models": ai_list_models(cfg),
                "base_url": cfg["base_url"]}

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

    # 单实例保护：Windows 上 SO_REUSEADDR 允许两个进程绑同一端口，
    # 结果请求被旧进程接走、新接口全 404 但页面看着是新的，极难排查。
    existing = probe_running()
    if existing is not None:
        print("")
        print("  写作台已经在运行了：http://127.0.0.1:%d" % PORT)
        if existing and os.path.normpath(existing) != os.path.normpath(ROOT):
            print("  注意：它服务的目录是 %s" % existing)
            print("  不是当前目录 %s" % ROOT)
        print("  这次不重复启动。已帮你打开浏览器，直接用那个窗口就行。")
        print("")
        if webbrowser:
            webbrowser.open("http://127.0.0.1:%d" % PORT)
        return

    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        print("")
        print("  端口 %d 绑不上：%s" % (PORT, e))
        print("  可能有别的程序占着这个端口。换个端口就改 tools/publish.py 里的 PORT。")
        print("")
        sys.exit(1)

    print("")
    print("  xiray 写作台已启动")
    print("  地址: http://127.0.0.1:%d" % PORT)
    print("  AI  : http://127.0.0.1:%d/ai" % PORT)
    print("  目录: %s" % ROOT)
    print("  关闭: 直接关掉这个窗口")
    print("")
    if webbrowser:
        threading.Timer(0.6, lambda: webbrowser.open("http://127.0.0.1:%d" % PORT)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def probe_running():
    """探测端口上是不是已经跑着一个写作台。返回它的 ROOT（空串表示无法确认），没在跑返回 None。"""
    try:
        req = urllib.request.Request("http://127.0.0.1:%d/api/info" % PORT)
        with urllib.request.urlopen(req, timeout=1.5) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        if isinstance(data, dict) and "root" in data and "site" in data:
            return data.get("root") or ""
    except Exception:
        pass
    return None


def main():
    if "--sync" in sys.argv:
        st = proxy_status(force=True)
        print("代理: %s%s" % (st["effective"], "" if st.get("ok") else "（探测不通，会降级直连）"))
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
