# xiray

个人博客。纯手写 HTML / CSS，无框架、无构建、无外部依赖，托管于 GitHub Pages。

## 目录结构

```
blog/
├── index.html          首页（带文章列表）
├── about.html          关于页
├── assets/
│   └── style.css       全站样式
├── posts/
│   ├── hello-github-pages.html
│   ├── floating-ball.html
│   └── annihilation-ring.html
├── .nojekyll           关掉 Jekyll 处理，原样发布
└── README.md
```

## 本地预览

直接双击 `index.html` 即可。没有构建步骤，不需要起服务器。

如果想让相对路径行为和生产环境完全一致，可以用 Python 起个静态服务：

```bash
python -m http.server 8080
# 然后打开 http://localhost:8080
```

## 加一篇新文章

1. 复制 `posts/floating-ball.html`，重命名为新文章的文件名（英文、短横线分隔）。
2. 改标题（`<title>` 和 `<h1>` 两处）、日期、正文。
3. 在 `index.html` 的文章列表里加一条 `<li>`，指向新文件。

正文里可以用的元素：`h2` / `h3`、`p`、`ul` / `ol`、`blockquote`、`pre > code`（代码块）、`code`（行内代码）、`img`。

## 部署

推送到 `main` 分支即可，GitHub Pages 会自动发布：

```bash
git add -A
git commit -m "post: 新文章标题"
git push
```

首次部署后，如果 Pages 还没开，去仓库 **Settings → Pages**，
Source 选 `Deploy from a branch`，Branch 选 `main` / `root`，保存。

## 注意

- **所有链接都用相对路径。** 项目页部署在 `/blog/` 子路径下，以 `/` 开头的绝对路径会 404。
- **不要删 `.nojekyll`。** 删了之后下划线开头的文件会被 Jekyll 吞掉。
