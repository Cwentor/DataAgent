/* 轻量 Markdown 渲染器（报告渲染用，白名单转义，无外部依赖）。
 *
 * 安全边界：所有插值先经 HTML 转义，仅生成白名单标签
 * （h1-h3/ul/ol/li/blockquote/code/pre/p/strong/em/table…），
 * 不支持原生 HTML 透传 —— 杜绝报告内容注入 XSS。
 */
(function () {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  /** 行内语法：`code`、**bold**、*italic*。 */
  function inline(text) {
    var s = esc(text);
    s = s.replace(/`([^`]+)`/g, function (_, c) { return "<code>" + c + "</code>"; });
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/\*([^*]+)\*/g, "<em>$1</em>");
    return s;
  }

  /**
   * 渲染 Markdown 文本为 HTML（块级：标题/列表/引用/代码块/表格/段落）。
   * @param {string} md
   * @returns {string}
   */
  function render(md) {
    var lines = String(md || "").split(/\r?\n/);
    var html = [];
    var i = 0;
    while (i < lines.length) {
      var line = lines[i];

      // 代码块 ```lang ... ```
      if (/^```/.test(line)) {
        var buf = [];
        i++;
        while (i < lines.length && !/^```/.test(lines[i])) { buf.push(lines[i]); i++; }
        i++; // 跳过收尾 ```
        html.push("<pre><code>" + esc(buf.join("\n")) + "</code></pre>");
        continue;
      }
      // 标题
      var m = /^(#{1,4})\s+(.*)$/.exec(line);
      if (m) {
        var level = Math.min(m[1].length + 1, 4); // # -> h2（报告主标题降一级）
        html.push("<h" + level + ">" + inline(m[2]) + "</h" + level + ">");
        i++; continue;
      }
      // 引用
      if (/^>\s?/.test(line)) {
        var q = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) { q.push(lines[i].replace(/^>\s?/, "")); i++; }
        html.push("<blockquote>" + inline(q.join(" ")) + "</blockquote>");
        continue;
      }
      // 无序/有序列表
      if (/^[-*]\s+/.test(line) || /^\d+\.\s+/.test(line)) {
        var ordered = /^\d+\.\s+/.test(line);
        var items = [];
        while (i < lines.length && (/^[-*]\s+/.test(lines[i]) || /^\d+\.\s+/.test(lines[i]))) {
          items.push(lines[i].replace(/^([-*]|\d+\.)\s+/, ""));
          i++;
        }
        var tag = ordered ? "ol" : "ul";
        html.push("<" + tag + ">" + items.map(function (x) { return "<li>" + inline(x) + "</li>"; }).join("") + "</" + tag + ">");
        continue;
      }
      // 表格 |a|b| / |---|---|
      if (/^\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
        var head = line.split("|").slice(1, -1).map(function (x) { return x.trim(); });
        i += 2;
        var rows = [];
        while (i < lines.length && /^\|.*\|\s*$/.test(lines[i])) {
          rows.push(lines[i].split("|").slice(1, -1).map(function (x) { return x.trim(); }));
          i++;
        }
        var t = "<table><thead><tr>" + head.map(function (h) { return "<th>" + inline(h) + "</th>"; }).join("") + "</tr></thead><tbody>";
        t += rows.map(function (r) { return "<tr>" + r.map(function (c) { return "<td>" + inline(c) + "</td>"; }).join("") + "</tr>"; }).join("");
        html.push(t + "</tbody></table>");
        continue;
      }
      // 空行
      if (!line.trim()) { i++; continue; }
      // 普通段落（连续非空行合并）
      var para = [line];
      i++;
      while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|[-*]\s|\d+\.\s|>|```|\|)/.test(lines[i])) {
        para.push(lines[i]); i++;
      }
      html.push("<p>" + inline(para.join(" ")) + "</p>");
    }
    return html.join("\n");
  }

  window.MdLite = { render: render, esc: esc };
})();
