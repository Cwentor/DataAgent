/* 产物渲染（AgentCanvas）：四视图内容 + 导出。
 *
 * 视图：执行报告（Markdown）/ 图表（ECharts 交互 + PNG 导出）/
 *       代码沙箱（Prism 高亮 + stdout）/ 数据审计（分块滚动表格）。
 * 视图切换由侧边栏（sidebar-ui.js）负责，本模块只负责内容渲染与导出；
 * 渲染模型：store.currentArtifacts 变更时全量重绘（产物量级小）；
 * ECharts 实例池按 host 复用，重绘前 dispose 防泄漏。
 */
(function () {
  "use strict";

  var charts = []; // [{instance, el}]
  var COLORS = ["#4f6ef7", "#22b8cf", "#12b886", "#f59f00", "#e64980", "#845ef7", "#74b816", "#f76707"];

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function fmt(v) {
    if (typeof v === "number") { return v.toLocaleString("zh-CN", { maximumFractionDigits: 2 }); }
    return String(v == null ? "" : v);
  }

  function disposeCharts() {
    charts.forEach(function (c) { try { c.instance.dispose(); } catch (e) { /* 忽略 */ } });
    charts = [];
  }

  /** 图表视图激活时调用：隐藏容器初始化尺寸为 0 的兜底。 */
  function resizeCharts() {
    charts.forEach(function (c) { try { c.instance.resize(); } catch (e) { /* 忽略 */ } });
  }

  // ------------------------------------------------------------ 轮次分组
  /** 按 turn 将产物分桶（保持入账顺序），返回 [{turn, text, items}] 升序。 */
  function groupByTurn(items) {
    var order = [];
    var map = {};
    items.forEach(function (it) {
      var key = it.turn || 0;
      if (!map[key]) { map[key] = { turn: key, text: it.turnText || "", items: [] }; order.push(key); }
      map[key].items.push(it);
    });
    order.sort(function (a, b) { return a - b; });
    return order.map(function (k) { return map[k]; });
  }

  /** 轮次标注文案：第 N 轮（无轮次语境的遗留产物归为「历史产物」）。 */
  function turnLabel(group) {
    return group.turn ? "第 " + group.turn + " 轮" : "历史产物";
  }

  /** 轮次提问原文（优先产物自带 turnText，回退 Store 轮次索引）。 */
  function turnQuery(group) {
    if (group.text) { return group.text; }
    if (window.AgentStore && group.turn) { return AgentStore.turnInfo(group.turn).text || ""; }
    return "";
  }

  /** 构建轮次分组容器（同视图内多轮产物各自成组，互不覆盖）。 */
  function turnGroupEl(group, gridCls) {
    var wrap = document.createElement("div");
    wrap.className = "turn-group";
    var head = document.createElement("div");
    head.className = "turn-group-head";
    var q = turnQuery(group);
    head.innerHTML = '<span class="tg-badge">' + esc(turnLabel(group)) + "</span>"
      + '<span class="tg-q" title="' + esc(q) + '">' + esc(q) + "</span>";
    wrap.appendChild(head);
    var grid = document.createElement("div");
    grid.className = gridCls;
    wrap.appendChild(grid);
    return { wrap: wrap, grid: grid };
  }

  // ------------------------------------------------------------ 报告 Tab
  function renderReports(arts) {
    var box = $("report-content");
    var empty = $("report-empty");
    if (!arts.reports.length) {
      box.classList.add("hidden"); empty.classList.remove("hidden");
      return;
    }
    empty.classList.add("hidden"); box.classList.remove("hidden");
    while (box.firstChild) { box.removeChild(box.firstChild); }
    // 每轮报告各自成块（第 N 轮 · 提问原文），不再只显示最后一份
    groupByTurn(arts.reports).forEach(function (group) {
      var block = document.createElement("div");
      block.className = "report-block";
      var q = turnQuery(group);
      block.innerHTML = '<div class="turn-group-head"><span class="tg-badge">'
        + esc(turnLabel(group)) + "</span>"
        + '<span class="tg-q" title="' + esc(q) + '">' + esc(q) + "</span></div>";
      var body = document.createElement("div");
      body.className = "report-body";
      // 一轮可能产多份报告（如重综合）：全部展开，不再丢弃
      group.items.forEach(function (a) { body.innerHTML += MdLite.render(a.content || ""); });
      block.appendChild(body);
      box.appendChild(block);
    });
  }

  // ------------------------------------------------------------ 图表 Tab
  function renderCharts(arts) {
    var box = $("charts-content");
    var empty = $("charts-empty");
    disposeCharts();
    if (!arts.charts.length) {
      box.classList.add("hidden"); empty.classList.remove("hidden");
      return;
    }
    empty.classList.add("hidden"); box.classList.remove("hidden");
    while (box.firstChild) { box.removeChild(box.firstChild); }
    groupByTurn(arts.charts).forEach(function (group) {
      var g = turnGroupEl(group, "charts-grid");
      group.items.forEach(function (artifact, idx) {
        var block = document.createElement("div");
        block.className = "chart-block";
        block.innerHTML = '<div class="chart-block-title">' + esc(artifact.title || ("图表 " + (idx + 1))) + "</div>"
          + '<div class="chart-host" data-host="1"></div>'
          + '<div class="chart-export-row"><button type="button" class="mini-btn" data-export="png">⬇ PNG</button></div>';
        g.grid.appendChild(block);
        block.querySelector('[data-export="png"]').addEventListener("click", function () {
          var entry = charts.find(function (c) { return c.el === host; });
          if (entry) {
            var a = document.createElement("a");
            a.href = entry.instance.getDataURL({ pixelRatio: 2, backgroundColor: "#fff" });
            a.download = (artifact.title || "chart") + ".png";
            a.click();
          }
        });
        var host = block.querySelector(".chart-host");
        if (window.echarts) {
          var instance = echarts.init(host);
          var option = artifact.content || {};
          if (!option.color) { option.color = COLORS; }
          instance.setOption(option);
          charts.push({ instance: instance, el: host });
        }
      });
      box.appendChild(g.wrap);
    });
  }

  // ------------------------------------------------------------ 代码沙箱 Tab
  function renderCodes(arts) {
    var box = $("code-content");
    var empty = $("code-empty");
    var codes = arts.codes;
    // python_sandbox 工具的入参代码也进入沙箱 Tab（tool 事件驱动，由 app.js push）
    if (!codes.length) {
      box.classList.add("hidden"); empty.classList.remove("hidden");
      return;
    }
    empty.classList.add("hidden"); box.classList.remove("hidden");
    while (box.firstChild) { box.removeChild(box.firstChild); }
    groupByTurn(codes).forEach(function (group) {
      var g = turnGroupEl(group, "code-group-body");
      group.items.forEach(function (code, idx) {
        var block = document.createElement("div");
        block.className = "code-block";
        block.innerHTML = '<div class="code-block-head"><span class="lang-tag">PYTHON</span>'
          + esc(code.title || ("analysis_" + (idx + 1) + ".py")) + "</div>"
          + "<pre><code class='language-python'>" + esc(code.content) + "</code></pre>"
          + (code.stdout
            ? '<div class="code-stdout"><span class="out-label">STDOUT / RESULT</span>' + esc(code.stdout) + "</div>"
            : "");
        g.grid.appendChild(block);
      });
      box.appendChild(g.wrap);
    });
    if (window.Prism) { Prism.highlightAllUnder(box); }
  }

  // ------------------------------------------------------------ 数据审计 Tab
  function renderTables(arts) {
    var box = $("data-content");
    var empty = $("data-empty");
    if (!arts.tables.length) {
      box.classList.add("hidden"); empty.classList.remove("hidden");
      return;
    }
    empty.classList.add("hidden"); box.classList.remove("hidden");
    while (box.firstChild) { box.removeChild(box.firstChild); }
    groupByTurn(arts.tables).forEach(function (group) {
      var g = turnGroupEl(group, "data-group-body");
      group.items.forEach(function (t, idx) {
        var block = document.createElement("div");
        block.className = "data-block";
        var rows = t.rows || [];
        var cols = t.columns || (rows[0] ? rows[0].map(function (_, i) { return "col" + (i + 1); }) : []);
        var totalLabel = t.totalRows != null ? fmt(t.totalRows) + " 行（预览 " + rows.length + "）" : fmt(rows.length) + " 行";
        var head = '<div class="data-block-head">' + esc(t.title || ("数据集 " + (idx + 1)))
          + '<span class="rows-tag">' + totalLabel + " × " + cols.length + " 列</span></div>";
        var html = head + '<div class="table-scroll"><table><thead><tr>';
        cols.forEach(function (c) { html += "<th>" + esc(c) + "</th>"; });
        html += "</tr></thead><tbody>";
        // 审计表上限 200 行（超限提示；完整数据走导出）
        rows.slice(0, 200).forEach(function (r) {
          html += "<tr>";
          r.forEach(function (v) {
            html += "<td" + (typeof v === "number" ? " class='num'" : "") + ">" + esc(fmt(v)) + "</td>";
          });
          html += "</tr>";
        });
        html += "</tbody></table></div>";
        if (rows.length > 200) {
          html += '<div class="rows-tag" style="padding:8px 14px">仅展示前 200 行，共 ' + fmt(rows.length) + " 行</div>";
        }
        block.innerHTML = html;
        g.grid.appendChild(block);
      });
      box.appendChild(g.wrap);
    });
  }

  // ------------------------------------------------------------ 导出
  function download(filename, content, mime) {
    var blob = new Blob([content], { type: mime });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 500);
  }

  function exportMarkdown() {
    var state = AgentStore.get();
    var groups = groupByTurn(state.currentArtifacts.reports);
    var md = "# DataAgent 分析报告\n";
    if (!groups.length) {
      md += "\n（本次会话未生成报告）\n";
    }
    // 逐轮导出（第 N 轮 · 提问原文 -> 该轮报告全文），不再只导出最后一份
    groups.forEach(function (group) {
      md += "\n\n---\n\n## " + (group.turn ? "第 " + group.turn + " 轮" : "历史产物")
        + (group.text ? " · " + group.text : "") + "\n\n";
      group.items.forEach(function (a) { md += (a.content || "") + "\n\n"; });
    });
    var tables = state.currentArtifacts.tables;
    if (tables.length) {
      md += "\n\n---\n\n# 数据摘要\n";
      groupByTurn(tables).forEach(function (group) {
        md += "\n## " + (group.turn ? "第 " + group.turn + " 轮" : "历史产物")
          + (group.text ? " · " + group.text : "") + "\n";
        group.items.forEach(function (t) {
          md += "\n### " + (t.title || "数据集") + "\n\n";
          md += "| " + (t.columns || []).join(" | ") + " |\n";
          md += "|" + (t.columns || []).map(function () { return "---"; }).join("|") + "|\n";
          (t.rows || []).slice(0, 30).forEach(function (r) {
            md += "| " + r.map(function (v) { return String(v); }).join(" | ") + " |\n";
          });
        });
      });
    }
    download("dataagent-report.md", md, "text/markdown");
  }

  function exportHtml() {
    var state = AgentStore.get();
    var groups = groupByTurn(state.currentArtifacts.reports);
    var body = "";
    if (!groups.length) {
      body = MdLite.render("# 分析报告\n\n（本次会话未生成报告）");
    }
    // 逐轮拼接（块间分隔线），与报告视图分组一致
    groups.forEach(function (group) {
      body += '<div class="turn-sec"><h2 class="turn-h">'
        + esc((group.turn ? "第 " + group.turn + " 轮" : "历史产物") + (group.text ? " · " + group.text : ""))
        + "</h2>";
      group.items.forEach(function (a) { body += MdLite.render(a.content || ""); });
      body += "</div>";
    });
    var html = "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
      + "<title>DataAgent 分析报告</title>"
      + "<style>body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;max-width:860px;margin:40px auto;padding:0 24px;line-height:1.8;color:#1a1d2e}"
      + "table{border-collapse:collapse;width:100%}th,td{border:1px solid #e6e8f0;padding:6px 10px;font-size:13px}"
      + "th{background:#f6f7fb}blockquote{border-left:3px solid #4f6ef7;background:#f6f8ff;padding:8px 14px;margin:10px 0}"
      + ".turn-sec{margin-bottom:34px;padding-bottom:20px;border-bottom:1px solid #e6e8f0}.turn-sec:last-child{border-bottom:none}"
      + ".turn-h{font-size:15px;color:#2f5fe0;background:#eef2ff;padding:8px 14px;border-radius:8px;border-left:3px solid #4f6ef7}"
      + "</style></head><body>" + body + "</body></html>";
    download("dataagent-report.html", html, "text/html");
  }

  // ------------------------------------------------------------ 入口
  function render(arts) {
    renderReports(arts);
    renderCharts(arts);
    renderCodes(arts);
    renderTables(arts);
  }

  window.AgentCanvas = {
    init: function () {
      $("export-md").addEventListener("click", exportMarkdown);
      $("export-html").addEventListener("click", exportHtml);
      AgentStore.subscribe("currentArtifacts", function (state) {
        render(state.currentArtifacts);
      });
    },
    /** 清空产物（视图切换由侧边栏负责）。改走 Store 直接清空并广播，
     *  避免绕过订阅者（reset 由调用方在 AgentStore.reset 之后调用时为空操作）。 */
    reset: function () {
      disposeCharts();
      var arts = AgentStore.get().currentArtifacts;
      arts.reports = []; arts.charts = []; arts.codes = []; arts.tables = [];
      render(arts);
    },
    resizeCharts: resizeCharts
  };
})();
