/* Header 附属 UI：DuckDB Schema 选择器 + 会话历史 / 新线程 CTA。
 *
 * - Schema：登录后从 /api/schema/summary 拉取语义目录字段清单（按表分组展示）；
 * - 线程：本机 localStorage 保存最近 20 条会话（问题摘要 + 时间 + 事件数），
 *   「新线程」重置工作区（store/画布/时间线），历史项点击回放问题到输入框。
 */
(function () {
  "use strict";

  var THREAD_KEY = "dataagent_threads";
  var MAX_THREADS = 20;

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function fmtTime(ts) {
    var d = new Date(ts);
    return (d.getMonth() + 1) + "/" + d.getDate() + " " +
      String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }

  // ---------------------------------------------------------------- 线程存储
  function loadThreads() {
    try { return JSON.parse(localStorage.getItem(THREAD_KEY) || "[]"); } catch (e) { return []; }
  }
  function saveThreads(list) {
    try { localStorage.setItem(THREAD_KEY, JSON.stringify(list.slice(0, MAX_THREADS))); } catch (e) { /* 忽略 */ }
  }
  function recordThread(query, eventCount) {
    var list = loadThreads().filter(function (t) { return t.q !== query; });
    list.unshift({ q: query, ts: Date.now(), events: eventCount || 0 });
    saveThreads(list);
  }
  function removeThread(ts) {
    saveThreads(loadThreads().filter(function (t) { return t.ts !== ts; }));
  }

  // ---------------------------------------------------------------- 下拉开关
  function togglePanel(panelId, btnId, otherPanelId, otherBtnId) {
    var panel = document.getElementById(panelId);
    var btn = document.getElementById(btnId);
    var isOpen = !panel.classList.contains("hidden");
    // 打开前先关另一个（互斥下拉）
    document.getElementById(otherPanelId).classList.add("hidden");
    document.getElementById(otherBtnId).setAttribute("aria-expanded", "false");
    panel.classList.toggle("hidden", isOpen);
    btn.setAttribute("aria-expanded", isOpen ? "false" : "true");
    return !isOpen; // 返回操作后的打开状态
  }

  function closeAllPanels() {
    document.getElementById("schema-panel").classList.add("hidden");
    document.getElementById("thread-panel").classList.add("hidden");
    document.getElementById("schema-btn").setAttribute("aria-expanded", "false");
    document.getElementById("thread-history-btn").setAttribute("aria-expanded", "false");
  }

  // ---------------------------------------------------------------- Schema 面板
  var schemaLoaded = false;

  function renderSchema(data) {
    var body = document.getElementById("schema-panel-body");
    if (!data || !data.tables || !data.tables.length) {
      body.innerHTML = '<div class="hp-empty">语义目录为空</div>';
      return;
    }
    var html = "";
    data.tables.forEach(function (t) {
      html += '<div class="schema-table-group">'
        + '<div class="schema-table-name">▤ ' + esc(t.table)
        + '<span class="t-count">' + t.fields.length + " 字段</span></div>";
      t.fields.forEach(function (f) {
        html += '<div class="schema-field">'
          + '<span class="f-logical">' + esc(f.field) + "</span>"
          + '<span class="f-column">' + esc(f.table === undefined ? f.column : f.column) + "</span>"
          + '<span class="f-dtype">' + esc(f.dtype) + "</span></div>";
      });
      html += "</div>";
    });
    body.innerHTML = html;
  }

  function loadSchema() {
    if (schemaLoaded) { return; }
    var headers = {};
    var token = sessionStorage.getItem("dataagent_token") || localStorage.getItem("dataagent_token") || "";
    if (token) { headers.Authorization = "Bearer " + token; }
    var sid = sessionStorage.getItem("dataagent_sid") || localStorage.getItem("dataagent_session") || "";
    if (sid) { headers["X-Session-ID"] = sid; }
    fetch("/api/schema/summary", { headers: headers })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        schemaLoaded = true;
        renderSchema(data);
      })
      .catch(function () {
        document.getElementById("schema-panel-body").innerHTML =
          '<div class="hp-empty">加载失败，请稍后重试</div>';
      });
  }

  // ---------------------------------------------------------------- 历史面板
  function renderThreads() {
    var body = document.getElementById("thread-panel-body");
    var threads = loadThreads();
    if (!threads.length) {
      body.innerHTML = '<div class="hp-empty">暂无历史会话</div>';
      return;
    }
    body.innerHTML = "";
    threads.forEach(function (t) {
      var item = document.createElement("div");
      item.className = "thread-item";
      item.innerHTML = '<span class="t-q" title="' + esc(t.q) + '">' + esc(t.q) + "</span>"
        + '<span class="t-time">' + fmtTime(t.ts) + "</span>"
        + '<button type="button" class="t-del" title="删除该条">✕</button>';
      item.querySelector(".t-q").addEventListener("click", function () {
        closeAllPanels();
        var input = document.getElementById("query");
        input.value = t.q;
        input.focus();
      });
      item.querySelector(".t-del").addEventListener("click", function (e) {
        e.stopPropagation();
        removeThread(t.ts);
        renderThreads();
      });
      body.appendChild(item);
    });
  }

  // ---------------------------------------------------------------- 新线程
  function newThread() {
    if (window.__activeStream) { window.__activeStream.abort(); }
    AgentStore.reset();
    if (window.AgentStreamUI) { AgentStreamUI.resetScroll(); }
    if (window.AgentCanvas) { AgentCanvas.reset(); }
    var input = document.getElementById("query");
    input.value = "";
    input.focus();
    closeAllPanels();
  }

  // ---------------------------------------------------------------- 入口
  function init() {
    var schemaBtn = document.getElementById("schema-btn");
    var threadBtn = document.getElementById("thread-history-btn");
    schemaBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var open = togglePanel("schema-panel", "schema-btn", "thread-panel", "thread-history-btn");
      if (open) { loadSchema(); }
    });
    threadBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var open = togglePanel("thread-panel", "thread-history-btn", "schema-panel", "schema-btn");
      if (open) { renderThreads(); }
    });
    document.getElementById("new-thread-btn").addEventListener("click", newThread);
    // 点击面板外区域关闭
    document.addEventListener("click", function (e) {
      if (!e.target.closest("#schema-picker") && !e.target.closest("#thread-picker")) {
        closeAllPanels();
      }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { closeAllPanels(); }
    });
  }

  window.AgentHeaderUI = {
    init: init,
    recordThread: recordThread,
    newThread: newThread
  };
})();
