/* ---------------------------------------------------------
   xiray — 代码块语言自动识别（写作台 / AI 面板共用）

   作用：` ``` ` 后面什么都没写时，把代码丢给本地服务的模型认一遍，
   认出来就给 <pre> 补上 data-lang，这样既上色、也有角标，
   发布出去的静态页也是提前认好写死的（不靠读者浏览器跑模型）。

   用法：
     AILang.detect(root, { onApplied: fn })   // 立刻扫一遍
     AILang.debounced(root, { onApplied: fn }, 1500)
   原则：
     · 只处理「没有 data-lang」且正文超过 12 字符的块；
     · 结果按内容哈希缓存（内存 + sessionStorage），同一段代码只问一次；
     · 认不出来（text）也缓存，避免反复重试白花钱；
     · 请求失败一律静默 —— 猜不出最多是没有角标，hl.js 的正则兜底还在。
   --------------------------------------------------------- */
(function (global) {
  "use strict";

  var ENDPOINT = "/api/ai/detect";
  var MIN_CHARS = 12;
  var MAX_BLOCKS = 6;
  var SS_KEY = "xiray.aiLang.v1";

  var mem = {};
  var busy = false;

  function hash(s) {
    var h = 5381;
    for (var i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;
    return "h" + h.toString(36) + "_" + s.length;
  }

  function cacheGet(key) {
    if (mem[key]) return mem[key];
    try {
      var raw = global.sessionStorage.getItem(SS_KEY);
      if (!raw) return "";
      var obj = JSON.parse(raw) || {};
      if (obj[key]) { mem[key] = obj[key]; return obj[key]; }
    } catch (e) {}
    return "";
  }

  function cacheSet(key, lang) {
    mem[key] = lang;
    try {
      var raw = global.sessionStorage.getItem(SS_KEY);
      var obj = raw ? (JSON.parse(raw) || {}) : {};
      obj[key] = lang;
      var keys = Object.keys(obj);
      if (keys.length > 200) { obj = {}; obj[key] = lang; }
      global.sessionStorage.setItem(SS_KEY, JSON.stringify(obj));
    } catch (e) {}
  }

  function codeOf(pre) {
    return pre.querySelector("code") || pre;
  }

  /* 给某个块补语言 + 立刻重新上色 */
  function apply(pre, lang) {
    if (!pre || !lang || lang === "text") return false;
    if (pre.getAttribute("data-lang")) return false;
    pre.setAttribute("data-lang", lang);
    pre.setAttribute("data-lang-src", "ai");
    if (global.HL && global.HL.tagLang) global.HL.tagLang(pre, lang, "ai");
    var code = codeOf(pre);
    if (code && code.classList) {
      var cls = "language-" + lang;
      if ((" " + code.className + " ").indexOf(" " + cls + " ") < 0) {
        code.className = (code.className ? code.className + " " : "") + cls;
      }
    }
    if (global.HL && code) {
      // 光标正在这个块里编辑时不要动 DOM，只记语言，离开时 paintPre 会用它
      if (pre.classList && pre.classList.contains("editing")) return true;
      HL.paint(code, code.textContent, lang);
      pre.setAttribute("data-hl", "1");
      pre.classList.add("hl-on");
      pre.classList.remove("editing");
      return true;
    }
    return false;
  }

  /* 找出还需要识别的块 */
  function pending(root) {
    var out = [];
    if (!root || !root.querySelectorAll) return out;
    var list = root.querySelectorAll("pre");
    for (var i = 0; i < list.length; i++) {
      var pre = list[i];
      if (pre.getAttribute("data-lang")) continue;
      var text = codeOf(pre).textContent || "";
      if (text.trim().length < MIN_CHARS) continue;
      out.push(pre);
      if (out.length >= MAX_BLOCKS) break;
    }
    return out;
  }

  function detect(root, opts) {
    opts = opts || {};
    if (!root || !root.querySelectorAll) return;
    if (global.__AI_LANG_OFF__) return;

    var pres = pending(root);
    if (!pres.length) return;

    var todo = [], keys = [], changed = false;
    for (var i = 0; i < pres.length; i++) {
      var text = codeOf(pres[i]).textContent || "";
      var key = hash(text);
      var hit = cacheGet(key);
      if (hit) {
        if (hit !== "text" && apply(pres[i], hit)) changed = true;
        continue;
      }
      todo.push(pres[i]);
      keys.push(key);
    }
    if (changed && opts.onApplied) opts.onApplied(pres);
    if (!todo.length || busy) return;

    busy = true;
    var blocks = todo.map(function (pre) { return codeOf(pre).textContent || ""; });

    fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ blocks: blocks })
    }).then(function (r) { return r.json(); }).then(function (res) {
      if (!res || !res.ok || !res.langs) return;
      var touched = false;
      for (var i = 0; i < todo.length; i++) {
        var lang = res.langs[i];
        if (!lang) continue;
        cacheSet(keys[i], lang);
        if (lang !== "text" && apply(todo[i], lang)) touched = true;
      }
      if (touched && opts.onApplied) opts.onApplied(todo);
      if (touched && opts.onDone) opts.onDone();
    }).catch(function () {
      /* 没配 key / 断网 / 模型忙 —— 什么都别做，正则兜底还在 */
    }).then(function () {
      busy = false;
    });
  }

  var timers = {};
  function debounced(root, opts, ms) {
    var k = (opts && opts.key) || "default";
    if (timers[k]) clearTimeout(timers[k]);
    timers[k] = setTimeout(function () {
      timers[k] = null;
      detect(root, opts);
    }, ms || 1500);
  }

  global.AILang = { detect: detect, debounced: debounced, pending: pending, apply: apply };
})(window);
