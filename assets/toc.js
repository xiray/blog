/* ---------------------------------------------------------
   文章目录（TOC）
   · 从 .prose 里抓 h2/h3/h4 生成，位置在正文右侧的空白带（≥70rem 才显示）
   · 标题没有 id 时按标题文字生成一个，锚点跳转和滚动高亮都用它
   · 少于 2 个标题就干脆不显示，免得只有一条还占地方
   · 纯前端，不进正文、不影响搜索索引和 RSS
   --------------------------------------------------------- */

(function (global) {
  "use strict";

  var MIN_HEADS = 2;
  var TOP_OFFSET = 120; // 判定「滚到这条了」的阈值，要略大于吸顶导航高度

  function slug(text) {
    var s = String(text || "").trim().replace(/\s+/g, "-")
      .replace(/[^\w\u4e00-\u9fa5-]/g, "");
    return "h-" + (s.slice(0, 40) || "sec");
  }

  function build() {
    var prose = document.querySelector(".prose");
    if (!prose) return;

    var heads = prose.querySelectorAll("h2, h3, h4");
    if (heads.length < MIN_HEADS) return;

    /* 以最小的标题级别为基准做缩进：有的文章用 h2 开头，有的直接从 h3 开始 */
    var base = 6;
    for (var i = 0; i < heads.length; i++) {
      var lv = parseInt(heads[i].tagName.charAt(1), 10);
      if (lv < base) base = lv;
    }

    var nav = document.createElement("nav");
    nav.className = "toc";
    nav.setAttribute("aria-label", "文章目录");

    var title = document.createElement("p");
    title.className = "toc-title";
    title.textContent = "目录";
    nav.appendChild(title);

    var ol = document.createElement("ol");
    var links = [];
    var used = {};

    for (var j = 0; j < heads.length; j++) {
      var h = heads[j];
      var id = h.id;
      if (!id) {
        id = slug(h.textContent);
        var n = 2;
        while (used[id]) id = slug(h.textContent) + "-" + n++;
        h.id = id;
      }
      used[id] = 1;

      var a = document.createElement("a");
      a.href = "#" + id;
      a.textContent = (h.textContent || "").trim();
      var depth = parseInt(h.tagName.charAt(1), 10) - base;
      if (depth > 0) a.className = "lv" + Math.min(depth + 2, 4);

      var li = document.createElement("li");
      li.appendChild(a);
      ol.appendChild(li);
      links.push(a);
    }
    nav.appendChild(ol);

    /* 把 article 包一层 grid 容器，目录占据右边那一列 */
    var article = prose.closest ? prose.closest("article") : null;
    if (!article) return;
    var row = document.createElement("div");
    row.className = "article-row";
    article.parentNode.insertBefore(row, article);
    row.appendChild(article);
    row.appendChild(nav);

    var main = article.closest ? article.closest("main") : null;
    if (main && main.classList) main.classList.add("has-toc");

    /* ---- 滚动时高亮当前小节 ---- */
    var ticking = false;
    function sync() {
      ticking = false;
      var line = global.scrollY + TOP_OFFSET;
      var idx = 0;
      for (var k = 0; k < heads.length; k++) {
        if (heads[k].getBoundingClientRect().top + global.scrollY <= line) idx = k;
      }
      for (var m = 0; m < links.length; m++) {
        if (m === idx) links[m].classList.add("active");
        else links[m].classList.remove("active");
      }
    }
    function onScroll() {
      if (ticking) return;
      ticking = true;
      global.requestAnimationFrame(sync);
    }

    global.addEventListener("scroll", onScroll, { passive: true });
    global.addEventListener("resize", onScroll, { passive: true });
    sync();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})(typeof window !== "undefined" ? window : this);
