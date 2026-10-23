/* ---------------------------------------------------------
   首页搜索与筛选
   · 关键词匹配：标题 / 摘要 / 类别 / 正文（来自 search-index.json）
   · 类别筛选、日期范围
   · 无 JS 时列表仍然完整可见（HTML 里就有），这里只是增强
   --------------------------------------------------------- */

(function () {
  "use strict";

  var list = document.getElementById("postList");
  var input = document.getElementById("searchInput");
  var clearBtn = document.getElementById("searchClear");
  var catFilter = document.getElementById("catFilter");
  var dateFrom = document.getElementById("dateFrom");
  var dateTo = document.getElementById("dateTo");
  var resultCount = document.getElementById("resultCount");
  var noResult = document.getElementById("noResult");

  if (!list || !input) return;

  var items = Array.prototype.slice.call(list.querySelectorAll("li[data-slug]"));
  var indexMap = {};

  /* ---------------- 索引 ---------------- */

  function loadIndex() {
    return fetch("search-index.json", { cache: "no-cache" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        (data.items || []).forEach(function (it) {
          indexMap[it.slug] = it;
        });
      })
      .catch(function () { /* 索引拿不到就只搜 DOM 里的可见文本 */ });
  }

  /* ---------------- 类别下拉 ---------------- */

  function fillCategories() {
    var seen = {};
    items.forEach(function (li) {
      var c = li.getAttribute("data-category");
      if (c) seen[c] = 1;
    });

    function render(cats) {
      var current = catFilter.value;
      var html = '<option value="">全部类别</option>';
      cats.forEach(function (c) {
        html += '<option value="' + c + '">' + c + "</option>";
      });
      catFilter.innerHTML = html;
      if (current) catFilter.value = current;
      var fromUrl = new URLSearchParams(location.search).get("cat");
      if (fromUrl && !current) catFilter.value = fromUrl;
    }

    fetch("categories.json", { cache: "no-cache" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var cats = (data.categories || []).filter(function (c) { return seen[c]; });
        Object.keys(seen).forEach(function (c) {
          if (cats.indexOf(c) < 0) cats.push(c);
        });
        render(cats);
      })
      .catch(function () { render(Object.keys(seen)); });
  }

  /* ---------------- 过滤 ---------------- */

  function haystackOf(li) {
    var slug = li.getAttribute("data-slug");
    var rec = indexMap[slug] || {};
    return [
      rec.title, rec.excerpt, rec.category, rec.text,
      li.textContent
    ].join(" ").toLowerCase();
  }

  function apply() {
    var kw = (input.value || "").trim().toLowerCase();
    var cat = catFilter.value;
    var f = dateFrom.value;
    var t = dateTo.value;
    var shown = 0;

    items.forEach(function (li) {
      var date = li.getAttribute("data-date") || "";
      var liCat = li.getAttribute("data-category") || "";
      var ok = true;

      if (kw && haystackOf(li).indexOf(kw) < 0) ok = false;
      if (ok && cat && liCat !== cat) ok = false;
      if (ok && f && date && date < f) ok = false;
      if (ok && t && date && date > t) ok = false;

      li.style.display = ok ? "" : "none";
      if (ok) shown++;
    });

    var filtering = !!(kw || cat || f || t);
    if (noResult) noResult.hidden = !(shown === 0 && items.length > 0 && filtering);
    if (resultCount) {
      resultCount.textContent = filtering
        ? shown + " / " + items.length + " 篇"
        : "";
    }
    if (clearBtn) clearBtn.hidden = !kw;
  }

  /* ---------------- 事件 ---------------- */

  var timer = null;
  input.addEventListener("input", function () {
    if (timer) clearTimeout(timer);
    timer = setTimeout(apply, 120);
  });

  [catFilter, dateFrom, dateTo].forEach(function (n) {
    n.addEventListener("input", apply);
    n.addEventListener("change", apply);
  });

  if (clearBtn) {
    clearBtn.addEventListener("click", function () {
      input.value = "";
      input.focus();
      apply();
    });
  }

  document.addEventListener("keydown", function (e) {
    if (e.key === "/" && document.activeElement !== input) {
      e.preventDefault();
      input.focus();
    }
    if (e.key === "Escape" && document.activeElement === input) {
      input.value = "";
      apply();
    }
  });

  /* ---------------- 启动 ---------------- */

  fillCategories();
  apply();
  loadIndex().then(apply);
})();
