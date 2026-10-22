# xiray

个人博客。纯手写 HTML / CSS，无框架、无构建、无外部依赖，托管于 GitHub Pages。

## 目录结构

```
blog/
├── index.html          首页（带文章列表）
├── about.html          关于页
├── assets/
│   └── style.css       全站样式
├── posts/              文章
├── tools/              本地写作台（不上线，只在本机跑）
│   ├── 写作台.bat       双击启动
│   ├── writer.html     写作界面
│   └── publish.py      本地服务：写文件 + 更新首页 + 提交推送
├── .nojekyll           关掉 Jekyll 处理，原样发布
└── README.md
```

## 用写作台加文章（推荐）

双击 `tools/写作台.bat`，浏览器会自动打开写作页面。

- 左边写 Markdown，右边实时预览，看到的就是成品
- 填标题、日期、短名、摘要
- 点「发布」，剩下的全自动：生成 `posts/短名.html` → 在首页插一条 → 提交 → 推送
- 约半分钟后线上生效

草稿会自动存在浏览器里，关掉窗口再打开还在。`Ctrl+Enter` 直接发布，`Ctrl+S` 手动存草稿。

写完不想发，直接关窗口就行。

### 手动改完文件，只想推送

```bash
python tools/publish.py --sync
```

自动探测可用代理 → 提交 → 推送。

### 代理是自动的

`publish.py` 每次推送前自己找代理：环境变量 → Windows 系统代理 → 常见本地端口（7890 / 7897 / 10809 / 64836 …），然后真的去连一次 `github.com`，谁能通就用谁，都不通就直连。

结果只作用于当次 git 命令，**不写进 `.git/config`**，所以代理换端口不用改任何东西。

## 加一篇新文章（手写方式）

1. 复制 `posts/floating-ball.html`，重命名为新文章的文件名（英文、短横线分隔）。
2. 改标题（`<title>` 和 `<h1>` 两处）、日期、正文。
3. 在 `index.html` 的文章列表里加一条 `<li>`，指向新文件。

正文里可以用的元素：`h2` / `h3`、`p`、`ul` / `ol`、`blockquote`、`pre > code`（代码块）、`code`（行内代码）、`img`。

## 本地预览

直接双击 `index.html` 即可。没有构建步骤，不需要起服务器。

如果想完全模拟生产环境的路径行为：

```bash
python -m http.server 8080
# 然后打开 http://localhost:8080
```

## 部署

推送到 `main` 分支即可，GitHub Pages 会自动发布。推荐用写作台或：

```bash
python tools/publish.py --sync
```

GitHub Pages 初始设置：仓库 **Settings → Pages**，Source 选 `Deploy from a branch`，Branch 选 `main` / `root`。

## 注意

- **所有链接都用相对路径。** 项目页部署在 `/blog/` 子路径下，以 `/` 开头的绝对路径会 404。
- **不要删 `.nojekyll`。** 删了之后下划线开头的文件会被 Jekyll 吞掉。
- **`tools/` 只在本机用。** 写作台的发布接口只监听 `127.0.0.1`，外网访问不到，也不往外发数据。
