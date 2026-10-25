/* ---------------------------------------------------------
   xiray — 极简语法高亮（零依赖）
   线上文章页 / 写作台 / AI 面板共用。

   用法：
     HL.tokens(text, lang) -> [{t:"kw", s:"int"}, {t:"", s:" "}, ...]
     HL.paint(el, text, lang)   把 token 结构填进 el（不经过 innerHTML，安全）
     HL.run(root)               扫描 root 下所有 pre > code 并上色
     HL.langOf(pre, code)       从 data-lang / class="language-x" / 内容推断语言

   ```` ```cpp ```` / ```` ```py ```` / ```` ```txt ```` / ```` ```随便写 ```` 都认：
     · 认识的别名 → 对应规则集
     · 不认识的自选名字 → generic 通用规则（注释/字符串/数字/关键字/类名/函数）
     · text / plain → 不上色（作者明确要纯文本）
     · txt / log → 先猜，猜不出就不上色

   设计原则：
   - 只做「看得清」的高亮，不做完整解析器；宁可漏色，不可错乱。
   - 所有规则只用非捕获组 (?:...)，这样一次交替正则就能定位 token。
   --------------------------------------------------------- */
(function (global) {
  "use strict";

  /* ---------------- 语言别名 ---------------- */
  var ALIAS = {
    c: "cpp", h: "cpp", cc: "cpp", cxx: "cpp", hpp: "cpp", hh: "cpp", hxx: "cpp",
    "c++": "cpp", cplusplus: "cpp", cpp: "cpp", cpp17: "cpp", cpp20: "cpp",
    js: "js", mjs: "js", cjs: "js", jsx: "js", javascript: "js", node: "js", nodejs: "js",
    ts: "ts", tsx: "ts", typescript: "ts", mts: "ts",
    py: "py", py3: "py", python: "py", python3: "py", ipython: "py", pyi: "py",
    java: "java", kotlin: "java", kt: "java", scala: "java", groovy: "java",
    go: "go", golang: "go",
    rs: "rust", rust: "rust",
    html: "html", htm: "html", xml: "html", svg: "html", xhtml: "html", vue: "html",
    svelte: "html", xsl: "html",
    css: "css", scss: "css", sass: "css", less: "css", stylus: "css",
    json: "json", json5: "json", jsonc: "json", geojson: "json",
    sh: "sh", bash: "sh", shell: "sh", zsh: "sh", console: "sh",
    cmd: "sh", bat: "sh", ps: "sh", ps1: "sh", powershell: "sh", pwsh: "sh", yaml: "sh", yml: "sh",
    sql: "sql", mysql: "sql", pgsql: "sql", postgres: "sql", sqlite: "sql", tsql: "sql",
    md: "md", markdown: "md",
    diff: "diff", patch: "diff",
    text: "text", plain: "text", plaintext: "text", "": "text",
    txt: "auto", log: "auto", output: "auto", out: "auto", terminal: "auto"
  };

  /* ---------------- 可复用片段 ---------------- */
  var COM_C = "//[^\\n]*|/\\*[\\s\\S]*?\\*/";                 // // 与 /* */
  var COM_H = "#[^\\n]*";                                     // # 注释
  var STR_DQ = '"(?:[^"\\\\\\n]|\\\\.)*"?';                    // "..."
  var STR_SQ = "'(?:[^'\\\\\\n]|\\\\.)*'?";                    // '...'
  var STR_BQ = "`(?:[^`\\\\]|\\\\.)*`?";                       // `...`
  var NUM = "\\b(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|\\d[\\d_]*(?:\\.\\d[\\d_]*)?(?:[eE][+-]?\\d+)?)[uUlLfFnJjSs]*\\b";
  var OP = "->\\*?|::|:=|=>|\\.\\.\\.|<<=?|>>=?|[-+*/%=<>!&|^~?:]+";
  var PUN = "[{}\\[\\]();,.]";

  function kw(list) { return "\\b(?:" + list + ")\\b"; }

  /* ---------------- 语言规则 ----------------
     每项 [class, 正则片段]，按优先级从高到低排列。
     class 对应 CSS 里的 .t-<class>。 */

  /* 通用兜底：不认识的「自选语言」走这一套 */
  var GENERIC_RULES = [
    ["com", COM_C + "|(?:^|[ \\t])#[^\\n]*"],
    ["str", STR_DQ + "|" + STR_SQ + "|" + STR_BQ],
    ["num", NUM],
    ["kw", kw("if|else|elif|for|while|do|switch|case|default|break|continue|return|function|func|fn|def|class|struct|enum|interface|trait|impl|import|from|include|using|namespace|package|new|delete|try|catch|except|finally|throw|raise|var|let|const|val|public|private|protected|static|final|void|int|float|double|char|bool|boolean|string|true|false|null|nil|None|True|False|async|await|yield|in|is|and|or|not|with|as|lambda|match|when|then|end|begin|type|extends|implements|override|virtual|abstract|self|this|super")],
    ["cls", "\\b[A-Z][A-Za-z0-9_]{1,}\\b"],
    ["fn", "\\b[A-Za-z_$][\\w$]*(?=\\s*\\()"],
    ["op", OP],
    ["pun", PUN]
  ];

  var SPEC = {
    cpp: {
      f: "gm",
      r: [
        ["pre", "^[ \\t]*#[ \\t]*(?:include|define|undef|pragma|if|ifdef|ifndef|elif|else|endif|error|warning|line)\\b.*$"],
        ["com", COM_C],
        ["str", STR_DQ + "|" + STR_SQ],
        ["num", NUM],
        ["kw", kw("alignas|alignof|and|asm|auto|break|case|catch|class|const|constexpr|consteval|constinit|const_cast|continue|decltype|default|delete|do|dynamic_cast|else|enum|explicit|export|extern|false|for|friend|goto|if|inline|mutable|namespace|new|noexcept|nullptr|operator|or|private|protected|public|register|reinterpret_cast|return|sizeof|static|static_assert|static_cast|struct|switch|template|this|throw|true|try|typedef|typeid|typename|union|using|virtual|volatile|while|override|final|co_await|co_return|co_yield|requires|concept|not|and_eq|or_eq|xor")],
        ["typ", kw("bool|char|char8_t|char16_t|char32_t|double|float|int|long|short|signed|unsigned|void|wchar_t|size_t|ssize_t|ptrdiff_t|int8_t|int16_t|int32_t|int64_t|uint8_t|uint16_t|uint32_t|uint64_t|string|wstring|u16string|u32string|vector|array|deque|forward_list|list|map|multimap|unordered_map|unordered_multimap|set|multiset|unordered_set|unordered_multiset|pair|tuple|queue|priority_queue|stack|bitset|valarray|shared_ptr|unique_ptr|weak_ptr|function|bind|make_pair|make_shared|make_unique|thread|mutex|lock_guard|unique_lock|istream|ostream|iostream|stringstream|ifstream|ofstream|fstream|std|FILE")],
        ["var", kw("cout|cin|cerr|clog|endl|printf|scanf|malloc|free|memset|memcpy|strlen|push_back|pop_back|emplace_back|begin|end|size|empty|first|second")],
        ["cls", "\\b[A-Z][A-Za-z0-9_]*\\b"],
        ["fn", "\\b[A-Za-z_]\\w*(?=\\s*\\()"],
        ["op", OP],
        ["pun", PUN]
      ]
    },

    java: {
      f: "gm",
      r: [
        ["com", COM_C],
        ["str", '"""[\\s\\S]*?"""|' + STR_DQ + "|" + STR_SQ],
        ["dec", "@[A-Za-z_]\\w*"],
        ["num", NUM],
        ["kw", kw("abstract|assert|break|byte|case|catch|char|class|const|continue|default|do|double|else|enum|extends|final|finally|float|for|goto|if|implements|import|instanceof|int|interface|long|native|new|package|private|protected|public|return|short|static|strictfp|super|switch|synchronized|this|throw|throws|transient|try|var|void|volatile|while|record|sealed|permits|yield|true|false|null")],
        ["typ", kw("String|Integer|Long|Double|Float|Boolean|Character|Byte|Short|Object|List|Map|Set|ArrayList|LinkedList|HashMap|HashSet|TreeMap|Queue|Deque|Stack|Optional|Stream|Collectors|Arrays|Collections|System|Math|Thread|Runnable|Exception|RuntimeException|IllegalArgumentException|StringBuilder|LocalDate|LocalDateTime|BigDecimal")],
        ["cls", "\\b[A-Z][A-Za-z0-9_]*\\b"],
        ["fn", "\\b[A-Za-z_]\\w*(?=\\s*\\()"],
        ["op", OP],
        ["pun", PUN]
      ]
    },

    ts: {
      f: "gm",
      r: [
        ["com", COM_C],
        ["str", STR_DQ + "|" + STR_SQ + "|" + STR_BQ],
        ["num", NUM],
        ["kw", kw("abstract|any|as|asserts|async|await|break|case|catch|class|const|constructor|continue|declare|default|delete|do|else|enum|export|extends|false|finally|for|from|function|get|if|implements|import|in|infer|instanceof|interface|is|keyof|let|namespace|new|null|of|override|private|protected|public|readonly|return|satisfies|set|static|super|switch|symbol|this|throw|true|try|type|typeof|undefined|unique|var|void|while|yield|never|unknown|string|number|boolean|bigint|object")],
        ["typ", kw("Array|Object|String|Number|Boolean|Function|Promise|Response|Request|Map|Set|WeakMap|WeakSet|Symbol|RegExp|JSON|Math|Date|Error|BigInt|HTMLElement|Element|Node|Event|Record|Partial|Readonly|Pick|Omit")],
        ["cls", "\\b[A-Z][A-Za-z0-9_]*\\b"],
        ["key", "[A-Za-z_$][\\w$]*(?=\\s*:(?!:))"],
        ["fn", "\\b[A-Za-z_$][\\w$]*(?=\\s*\\()"],
        ["op", OP],
        ["pun", PUN]
      ]
    },

    js: {
      f: "gm",
      r: [
        ["com", COM_C],
        ["str", STR_DQ + "|" + STR_SQ + "|" + STR_BQ],
        ["num", NUM],
        ["kw", kw("async|await|break|case|catch|class|const|continue|debugger|default|delete|do|else|export|extends|false|finally|for|from|function|get|if|import|in|instanceof|let|new|null|of|return|set|static|super|switch|this|throw|true|try|typeof|undefined|var|void|while|with|yield")],
        ["typ", kw("Array|Object|String|Number|Boolean|Function|Promise|Map|Set|WeakMap|WeakSet|Symbol|RegExp|JSON|Math|Date|Error|BigInt|Proxy|Reflect|Intl|document|window|console|module|exports|require")],
        ["cls", "\\b[A-Z][A-Za-z0-9_]*\\b"],
        ["key", "[A-Za-z_$][\\w$]*(?=\\s*:(?!:))"],
        ["fn", "\\b[A-Za-z_$][\\w$]*(?=\\s*\\()"],
        ["op", OP],
        ["pun", PUN]
      ]
    },

    py: {
      f: "gm",
      r: [
        ["com", COM_H],
        ["str", "(?:[rRbBuUfF]{0,2})(?:\\\"\\\"\\\"[\\s\\S]*?\\\"\\\"\\\"|'''[\\s\\S]*?'''|" + STR_DQ + "|" + STR_SQ + ")"],
        ["dec", "^[ \\t]*@[\\w.]+"],
        ["num", NUM],
        ["kw", kw("and|as|assert|async|await|break|class|continue|def|del|elif|else|except|finally|for|from|global|if|import|in|is|lambda|match|nonlocal|not|or|pass|raise|return|try|while|with|yield|case")],
        ["typ", kw("int|float|str|bool|list|dict|set|tuple|bytes|bytearray|object|type|range|len|enumerate|zip|map|filter|sum|min|max|print|open|sorted|reversed|isinstance|getattr|setattr|hasattr|super|None|True|False|Exception|ValueError|TypeError|KeyError|IndexError|RuntimeError|StopIteration")],
        ["var", kw("self|cls")],
        ["cls", "\\b[A-Z][A-Za-z0-9_]*\\b"],
        ["fn", "\\b[A-Za-z_]\\w*(?=\\s*\\()"],
        ["op", OP],
        ["pun", PUN]
      ]
    },

    go: {
      f: "gm",
      r: [
        ["com", COM_C],
        ["str", STR_BQ + "|" + STR_DQ + "|" + STR_SQ],
        ["num", NUM],
        ["kw", kw("break|case|chan|const|continue|default|defer|else|fallthrough|for|func|go|goto|if|import|interface|map|package|range|return|select|struct|switch|type|var")],
        ["typ", kw("bool|byte|complex64|complex128|error|float32|float64|int|int8|int16|int32|int64|rune|string|uint|uint8|uint16|uint32|uint64|uintptr|true|false|nil|iota")],
        ["var", kw("make|new|len|cap|append|copy|delete|panic|recover|print|println|fmt|Errorf|Println|Printf|Sprintf")],
        ["cls", "\\b[A-Z][A-Za-z0-9_]*\\b"],
        ["fn", "\\b[A-Za-z_]\\w*(?=\\s*\\()"],
        ["op", OP],
        ["pun", PUN]
      ]
    },

    rust: {
      f: "gm",
      r: [
        ["com", COM_C],
        ["str", 'r?#*"(?:[^"\\\\]|\\\\.)*"#*|' + STR_DQ + "|'(?:[^'\\\\\\n]|\\\\.)'"],
        ["dec", "#!?\\[[^\\]\\n]*\\]"],
        ["num", NUM],
        ["kw", kw("as|async|await|break|const|continue|crate|dyn|else|enum|extern|false|fn|for|if|impl|in|let|loop|match|mod|move|mut|pub|ref|return|self|Self|static|struct|super|trait|true|type|unsafe|use|where|while|macro_rules")],
        ["typ", kw("i8|i16|i32|i64|i128|isize|u8|u16|u32|u64|u128|usize|f32|f64|bool|char|str|String|Vec|VecDeque|Option|Result|Box|Rc|Arc|RefCell|HashMap|HashSet|BTreeMap|Some|None|Ok|Err")],
        ["var", kw("println|print|format|vec|assert|assert_eq|panic|unwrap|expect|iter|into|clone")],
        ["cls", "\\b[A-Z][A-Za-z0-9_]*\\b"],
        ["fn", "\\b[A-Za-z_]\\w*(?=\\s*[(!])"],
        ["op", OP],
        ["pun", PUN]
      ]
    },

    html: {
      f: "gm",
      r: [
        ["com", "<!--[\\s\\S]*?-->"],
        ["meta", "<!doctype[^>]*>|<\\?[\\s\\S]*?\\?>|<![A-Z][^>]*>"],
        ["tag", "</?[A-Za-z][\\w:-]*"],
        ["str", STR_DQ + "|" + STR_SQ],
        ["attr", "\\b[A-Za-z_:][-\\w:.]*(?=\\s*=)"],
        ["pun", "[<>/=]+"]
      ]
    },

    css: {
      f: "gm",
      r: [
        ["com", "/\\*[\\s\\S]*?\\*/"],
        ["str", STR_DQ + "|" + STR_SQ],
        ["at", "@[\\w-]+"],
        ["key", "[-a-zA-Z]+(?=\\s*:)"],
        ["num", "#[0-9a-fA-F]{3,8}\\b|\\b\\d+(?:\\.\\d+)?(?:px|em|rem|ex|ch|vw|vh|vmin|vmax|%|s|ms|deg|turn|fr|pt)?\\b"],
        ["sel", "[.#][-\\w]+|::?[a-zA-Z-]+(?=[\\s,{:.\\[])"],
        ["var", "--[-\\w]+"],
        ["pun", "[{}:;,()>+~*]+"]
      ]
    },

    json: {
      f: "gm",
      r: [
        ["key", '"(?:[^"\\\\]|\\\\.)*"(?=\\s*:)'],
        ["str", STR_DQ],
        ["num", "-?\\b\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?\\b"],
        ["kw", "\\b(?:true|false|null)\\b"],
        ["pun", "[{}\\[\\],:]+"]
      ]
    },

    sh: {
      f: "gm",
      r: [
        ["com", "(?:^|[ \\t])#[^\\n]*"],
        ["str", STR_DQ + "|" + STR_SQ],
        ["var", "\\$\\{[^}\\n]*\\}|\\$[A-Za-z_][\\w]*|\\$[0-9@#?$!*-]"],
        ["kw", kw("if|then|elif|else|fi|for|while|until|do|done|case|esac|function|return|in|local|export|readonly|declare|set|unset|shift|break|continue|exit|source|trap|select|time")],
        ["bi", kw("echo|printf|cd|pwd|ls|cp|mv|rm|mkdir|rmdir|cat|less|more|head|tail|grep|egrep|rg|sed|awk|find|xargs|sort|uniq|wc|tee|touch|chmod|chown|ln|tar|zip|unzip|curl|wget|git|npm|pnpm|yarn|node|python|python3|pip|pip3|make|cmake|gcc|clang|go|cargo|rustc|java|javac|docker|sudo|apt|apt-get|brew|systemctl|ssh|scp|rsync|ps|kill|killall|top|df|du|env|which|man|true|false|read|test|nohup")],
        ["op", OP],
        ["pun", "[{}()\\[\\];|&]+"]
      ]
    },

    sql: {
      f: "gim",
      r: [
        ["com", "--[^\\n]*|/\\*[\\s\\S]*?\\*/"],
        ["str", "'(?:[^'\\\\]|\\\\.|'')*'?"],
        ["kw", kw("select|from|where|insert|into|values|value|update|set|delete|create|table|alter|drop|truncate|index|view|join|left|right|inner|outer|full|cross|natural|on|group|by|order|having|limit|offset|union|all|as|and|or|not|null|is|in|between|like|regexp|distinct|case|when|then|else|end|primary|key|foreign|references|default|auto_increment|unique|exists|begin|start|transaction|commit|rollback|count|sum|avg|min|max|cast|coalesce|ifnull|desc|asc|with|returning|grant|revoke|explain|analyze")],
        ["typ", kw("int|integer|bigint|smallint|tinyint|decimal|numeric|float|double|real|char|varchar|text|blob|date|datetime|timestamp|time|year|boolean|json")],
        ["num", "\\b\\d+(?:\\.\\d+)?\\b"],
        ["var", "`[^`\\n]*`|@[A-Za-z_]\\w*"],
        ["op", ">=|<=|<>|!=|[-+*/%=<>]+"],
        ["pun", "[(),;.]+"]
      ]
    },

    md: {
      f: "gm",
      r: [
        ["fence", "^[ \\t]*```.*$"],
        ["h", "^#{1,6}[ \\t].*$"],
        ["quote", "^[ \\t]*>.*$"],
        ["li", "^[ \\t]*(?:[-*+]|\\d+\\.)[ \\t]"],
        ["hr", "^[ \\t]*(?:---+|\\*\\*\\*+|___+)[ \\t]*$"],
        ["bold", "\\*\\*[^*\\n]+\\*\\*|__[^_\\n]+__"],
        ["str", "`[^`\\n]+`|\\[[^\\]\\n]*\\]\\([^)\\n]*\\)"],
        ["fn", "!?\\[[^\\]\\n]*\\]|^[ \\t]*\\[[^\\]\\n]+\\]:"]
      ]
    },

    diff: {
      f: "gm",
      r: [
        ["meta", "^diff [^\\n]*"],
        ["com", "^@@[^\\n]*$"],
        ["str", "^\\+[^\\n]*$"],
        ["pre", "^-[^\\n]*$"]
      ]
    },

    generic: { f: "gm", r: GENERIC_RULES },
    auto: { f: "gm", r: GENERIC_RULES },
    text: { f: "gm", r: [] }
  };

  /* ---------------- 编译 ---------------- */
  var COMPILED = {};
  function compiled(name) {
    if (COMPILED[name]) return COMPILED[name];
    var spec = SPEC[name] || SPEC.generic;
    if (!spec.r.length) return (COMPILED[name] = null);
    var names = [], srcs = [];
    for (var i = 0; i < spec.r.length; i++) {
      names.push(spec.r[i][0]);
      srcs.push("(" + spec.r[i][1] + ")");
    }
    return (COMPILED[name] = { names: names, rx: new RegExp(srcs.join("|"), spec.f || "gm") });
  }

  function normLang(lang) {
    var k = String(lang == null ? "" : lang).toLowerCase().trim();
    if (!k) return "text";
    // 容忍 pandoc 写法：{.cpp} / .cpp
    k = k.replace(/^\{?\s*\.?/, "").replace(/\s*\}?$/, "").trim();
    if (k === "auto") return "auto";
    if (ALIAS.hasOwnProperty(k)) return ALIAS[k];
    if (SPEC.hasOwnProperty(k)) return k;
    return "generic";        // 自选语言名：用通用规则，总比一片灰强
  }

  /* ---------------- 分词 ----------------
     VS Code 把「控制流」和「声明」分两种颜色、方法调用和函数定义分两种颜色、
     括号单独一色。规则表里没必要为每个语言各写一遍，分词完再按内容/上下文改判一次即可。 */
  var CONTROL_KW = /^(?:if|else|elif|elseif|for|foreach|while|do|switch|case|default|break|continue|return|goto|try|catch|except|finally|throw|raise|yield|await|defer|go|select|when|match|loop|until|fi|then|esac|done|in|as|not|and|or|is|lambda|assert|del|pass|with|new|delete)$/;
  var BRACKET = "(){}[]";

  function reclassify(out) {
    for (var i = 0; i < out.length; i++) {
      var t = out[i];
      if (!t.t) continue;
      if (t.t === "kw" && CONTROL_KW.test(t.s)) { t.t = "ctl"; continue; }
      if (t.t === "pun" && t.s.length === 1 && BRACKET.indexOf(t.s) >= 0) { t.t = "br"; continue; }
      if (t.t === "fn") {
        // 前面隔着空白是 . 或 -> 就说明是方法调用（VS Code 里调用和定义不同色）
        var j = i - 1;
        while (j >= 0 && !out[j].s.trim()) j--;
        if (j >= 0) {
          var prev = out[j].s;
          if (/[.]$/.test(prev) || /->$/.test(prev)) t.t = "call";
        }
      }
    }
    return out;
  }

  function tokens(text, lang) {
    var s = String(text == null ? "" : text);
    var L = normLang(lang);
    if (L === "auto") L = guess(s);          // txt / log：先猜，猜不出就纯文本
    var c = compiled(L);
    if (!c || !s) return [{ t: "", s: s }];

    var out = [], i = 0;
    c.rx.lastIndex = 0;
    var guard = 0;
    while (i < s.length && guard++ < 200000) {
      c.rx.lastIndex = i;
      var m = c.rx.exec(s);
      if (!m) break;
      if (m.index > i) out.push({ t: "", s: s.slice(i, m.index) });
      var cls = "";
      for (var g = 1; g < m.length; g++) {
        if (m[g] !== undefined) { cls = c.names[g - 1]; break; }
      }
      out.push({ t: cls, s: m[0] });
      i = c.rx.lastIndex;
      if (c.rx.lastIndex <= m.index) i = m.index + 1; // 防零宽死循环
    }
    if (i < s.length) out.push({ t: "", s: s.slice(i) });
    return reclassify(out);
  }

  /* ---------------- 没写语言时保守推断 ----------------
     顺序很讲究：C 系的结构特征要排在 shell 前面，否则一段带 `&&`、
     `->` 的 C++ 会被判成 shell（`&&` 在 C/JS/PHP 里到处都是，不能单独当证据）。 */
  function guess(s) {
    var t = String(s || "");
    if (!t.trim()) return "text";
    if (/^[ \t]*#[ \t]*(?:include|define|pragma|ifndef|ifdef)\b/m.test(t) || /std::|#include\s*</.test(t)) return "cpp";
    if (/\bfn\s+\w+\s*[(<]|\blet\s+mut\b|\bimpl\s+\w+|\bpub\s+(?:fn|struct)\b/.test(t)) return "rust";
    if (/System\.out\.print|public\s+static\s+void\s+main|\b(?:public|private|protected)\s+(?:static\s+)?(?:final\s+)?class\s+\w+/.test(t)) return "java";
    /* C / C++ 的结构特征 */
    if (/\b(?:public|private|protected)\s*:/.test(t) ||
        /\b\w+\s*::\s*[~\w]/.test(t) ||
        /\b(?:int|char|void|bool|double|float|long|unsigned|size_t|string|vector)\b[^;\n]{0,40}[*&]\s*\w+\s*[=;,)\[]/.test(t) ||
        (/\w\s*->\s*\w/.test(t) && /;\s*$/m.test(t) && !/\bfunction\b|=>|\.\w+\s*\(/.test(t))) return "cpp";
    if (/^[ \t]*(?:def|class)\s+\w+|^[ \t]*(?:from\s+[\w.]+\s+)?import\s+[\w.]+/m.test(t) && !/\{[\s\S]*;\s*$/m.test(t)) return "py";
    if (/^\s*package\s+\w+\s*$/m.test(t) && /\bfunc\s+\w+\s*\(/.test(t)) return "go";
    if (/^diff --git |^@@ -\d+/m.test(t)) return "diff";
    if (/<!--|<\/[a-zA-Z][\w:-]*>/.test(t)) return "html";
    if (/^\s*[[{][\s\S]*"[^"]*"\s*:/.test(t) && !/[;()]/.test(t)) return "json";
    if (/\b(?:SELECT|FROM|WHERE|INSERT\s+INTO|UPDATE\s+\w+\s+SET)\b/i.test(t) &&
        /\b(?:SELECT|FROM|WHERE)\b/.test(t.toUpperCase())) return "sql";
    /* shell：只认行首的已知命令 / shebang / shell 独有写法，别拿 `&&` 当证据 */
    if (/^#!\s*\S*\b(?:ba|z|da|k)?sh\b/.test(t) ||
        /^[ \t]*(?:\$ |# )?(?:npm|pnpm|npx|yarn|git|sudo|cd|ls|ll|mkdir|rm|cp|mv|curl|wget|ssh|scp|tar|unzip|apt|apt-get|brew|docker|kubectl|systemctl|chmod|chown|export|source|python3?|pip3?|node|go|cargo|make|cmake|gcc|g\+\+|java|mvn|gradle)\s+\S/m.test(t) ||
        /\|\s*(?:grep|awk|sed|head|tail|sort|uniq|xargs|tee)\b|2>&1|>\/dev\/null/.test(t)) return "sh";
    if (/\b(?:const|let|var)\s+[\w${]+\s*=|\bfunction\s*\w*\s*\(|=>\s*[{(]|console\.log\b/.test(t)) return "js";
    if (/^\s*(?:[.#][\w-]+|[a-z][\w-]*)\s*[,>]?[\s\S]*\{[^}]*[-a-z]+\s*:\s*[^;]+;/.test(t) &&
        !/\bfunction\b|=>/.test(t)) return "css";
    return "text";
  }

  /* ---------------- DOM 渲染 ---------------- */
  function paint(el, text, lang) {
    var toks = tokens(text, lang);
    var frag = document.createDocumentFragment();
    for (var i = 0; i < toks.length; i++) {
      var tk = toks[i];
      if (!tk.t) {
        frag.appendChild(document.createTextNode(tk.s));
      } else {
        var sp = document.createElement("span");
        sp.className = "t-" + tk.t;
        sp.textContent = tk.s;
        frag.appendChild(sp);
      }
    }
    while (el.firstChild) el.removeChild(el.firstChild);
    el.appendChild(frag);
  }

  var LANG_IN_CLASS = /language-([\w+#.\u4e00-\u9fa5-]+)/i;

  /* 语言名清洗：允许中文/字母/数字/+#.-，容忍 {.cpp} 写法 */
  function cleanLangName(raw) {
    var k = String(raw || "").trim();
    var m = k.match(/^\{?\s*\.?([\w+#.\u4e00-\u9fa5-]+)\s*\}?$/);
    if (m) return m[1].toLowerCase();
    return k.replace(/[^\w+#.\u4e00-\u9fa5-]/g, "").toLowerCase();
  }

  function langOf(pre, code) {
    var raw = "";
    if (pre && pre.getAttribute) raw = pre.getAttribute("data-lang") || "";
    if (!raw && code && code.getAttribute) raw = code.getAttribute("data-lang") || "";
    if (!raw && code && code.getAttribute) {
      var m = (code.getAttribute("class") || "").match(LANG_IN_CLASS);
      if (m) raw = m[1];
    }
    if (!raw && pre && pre.getAttribute) {
      var m2 = (pre.getAttribute("class") || "").match(LANG_IN_CLASS);
      if (m2) raw = m2[1];
    }
    raw = cleanLangName(raw);
    if (raw) return normLang(raw);
    var text = code ? code.textContent : (pre ? pre.textContent : "");
    return normLang(guess(text));
  }

  /* ---------------- 语言角标 + 复制按钮 ---------------- */
  var LABELS = {
    cpp: "C++", c: "C", cs: "C#", java: "Java", py: "Python", js: "JavaScript",
    ts: "TypeScript", go: "Go", rust: "Rust", sql: "SQL", sh: "Shell",
    json: "JSON", html: "HTML", css: "CSS", md: "Markdown", diff: "Diff",
    php: "PHP", rb: "Ruby", kt: "Kotlin", swift: "Swift", yaml: "YAML"
  };

  /* 规范 id → 展示名（没登记的就把 id 大写） */
  function label(lang) {
    var k = String(lang || "").toLowerCase();
    return LABELS[k] || k.toUpperCase();
  }

  /* 把语言写进 pre：data-lang 给分词用，data-lang-label 给角标显示，
     data-lang-src 记来源（ai / auto / 空=作者自己写的）。
     注意 auto 推断只写 label、不写 data-lang —— 否则写作台导出 md 时
     会把「猜出来的语言」当成作者声明写进 ``` 围栏里。 */
  function tagLang(pre, lang, src) {
    if (!pre || !lang || lang === "text" || !pre.setAttribute) return;
    pre.setAttribute("data-lang-label", label(lang));
    if (src === "auto") return;
    if (!pre.getAttribute("data-lang")) pre.setAttribute("data-lang", lang);
    if (src) pre.setAttribute("data-lang-src", src);
  }

  /* 复制按钮：必须挂到 pre 外面 —— pre 是 overflow-x:auto 的滚动容器，
     里面的绝对定位元素会跟着横向滚动跑掉。 */
  function mountCopy(pre) {
    if (!pre || !pre.parentNode) return;
    if (pre.getAttribute("data-copy") === "1") return;
    var box = pre.parentNode;
    if (!(box.classList && box.classList.contains("code-block"))) {
      var wrap = document.createElement("div");
      wrap.className = "code-block";
      box.insertBefore(wrap, pre);
      wrap.appendChild(pre);
      box = wrap;
    }
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "code-copy";
    btn.textContent = "复制";
    btn.setAttribute("aria-label", "复制代码");
    box.appendChild(btn);
    pre.setAttribute("data-copy", "1");
  }

  /* 复制：优先走 execCommand（同步、在用户手势里，谁都能用），
     失败再交给 Clipboard API。反过来会踩坑 —— 异步回调里已经
     失去 transient activation，execCommand 会被拒。 */
  function copyText(text, cb) {
    var viaLegacy = function () {
      try {
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.setAttribute("readonly", "");
        ta.style.cssText = "position:fixed;top:0;left:-9999px;opacity:0";
        document.body.appendChild(ta);
        ta.select();
        ta.setSelectionRange(0, text.length);
        var done = document.execCommand("copy");
        document.body.removeChild(ta);
        return !!done;
      } catch (e) {
        return false;
      }
    };
    if (viaLegacy()) { cb(true); return; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () { cb(true); },
        function () { cb(false); }
      );
      return;
    }
    cb(false);
  }

  if (typeof document !== "undefined" && document.addEventListener) {
    document.addEventListener("click", function (e) {
      var t = e.target;
      var btn = t && t.closest ? t.closest(".code-copy") : null;
      if (!btn) return;
      var box = btn.parentNode;
      var pre = box ? box.querySelector("pre") : null;
      if (!pre) return;
      var code = pre.querySelector("code") || pre;
      copyText(code.textContent, function (okd) {
        btn.textContent = okd ? "已复制" : "复制失败";
        if (okd) btn.classList.add("done");
        clearTimeout(btn.__t);
        btn.__t = setTimeout(function () {
          btn.textContent = "复制";
          btn.classList.remove("done");
        }, 1500);
      });
      e.preventDefault();
      e.stopPropagation();
    }, true);
  }

  function run(root) {
    var box = root || document;
    var list = box.querySelectorAll ? box.querySelectorAll("pre > code") : [];
    for (var i = 0; i < list.length; i++) {
      var code = list[i];
      var pre = code.parentNode;
      if (!pre || !pre.getAttribute) continue;
      if (pre.getAttribute("data-hl") === "1") continue;
      pre.setAttribute("data-hl", "1");
      var declared = !!(pre.getAttribute("data-lang") ||
        (code.getAttribute("class") || "").match(LANG_IN_CLASS));
      var lang = langOf(pre, code);
      if (lang !== "text") {
        tagLang(pre, lang, declared ? pre.getAttribute("data-lang-src") || "" : "auto");
        paint(code, code.textContent, lang);
      }
      mountCopy(pre);
    }
  }

  var HL = {
    tokens: tokens,
    paint: paint,
    run: run,
    langOf: langOf,
    guess: guess,
    normLang: normLang,
    cleanLangName: cleanLangName,
    label: label,
    tagLang: tagLang,
    mountCopy: mountCopy,
    alias: ALIAS,
    langs: Object.keys(SPEC)
  };

  global.HL = HL;

  /* 线上文章页：DOM 就绪后自动上色（写作台会关掉这个，自己控制时机） */
  if (!global.__HL_MANUAL__) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () { run(document); });
    } else {
      run(document);
    }
  }
})(typeof window !== "undefined" ? window : this);
