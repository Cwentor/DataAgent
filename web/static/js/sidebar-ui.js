/* 左侧边栏 UI（AgentSidebarUI）。
 *
 * 职责：
 * - 视图切换（主区顶部 Tab 条）：「对话 / 执行报告 / 图表 / 代码沙箱 / 数据审计」
 *   五视图绑定当前会话，切换会话即切换整套产物；
 * - 产物徽标：各产物视图的条目计数（新产物入账即亮起）；
 * - 知识目录：语义目录字段清单（懒加载弹出面板）；
 * - 会话历史：本机 localStorage 最近 20 条，每条绑定自己的一份整轮快照
 *   （时间线 + 计划 + 产物 + 报告）；点击历史会话恢复对应内容，
 *   开新对话不清除旧对话；快照随工作区变更自动持久化（防抖归档），
 *   localStorage 超限时从最旧会话起降级丢快照；
 * - 新对话：中断当前流、归档当前会话快照后重置工作区。
 */
(function () {
  "use strict";

  var THREAD_KEY = "dataagent_threads";
  var ACTIVE_KEY = "dataagent_active_thread";
  var MAX_THREADS = 20;
  var ARCHIVE_DEBOUNCE_MS = 800;

  var VIEW_TITLES = {
    chat: "对话",
    report: "执行报告",
    charts: "图表",
    code: "代码沙箱",
    data: "数据审计"
  };
  var ARTIFACT_BADGES = { report: "reports", charts: "charts", code: "codes", data: "tables" };
  var currentView = "chat";
  var activeThreadId = ""; // 当前会话 id（空串 = 空白新会话，未登记历史）

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function fmtTime(ts) {
    var d = new Date(ts);
    return (d.getMonth() + 1) + "/" + d.getDate() + " " +
      String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }

  // ---------------------------------------------------------------- 视图切换（顶部 Tab 条）
  function showView(name) {
    if (!VIEW_TITLES[name]) { return; }
    currentView = name;
    document.querySelectorAll(".view-tab").forEach(function (btn) {
      btn.classList.toggle("active", btn.dataset.view === name);
    });
    document.querySelectorAll(".main-view").forEach(function (v) {
      v.classList.toggle("active", v.id === "view-" + name);
    });
    // 导出按钮只在产物视图出现
    var exportRow = document.getElementById("export-row");
    if (exportRow) { exportRow.classList.toggle("hidden", name === "chat"); }
    // 图表视图激活时 resize（隐藏容器初始化尺寸为 0 的情况）
    if (name === "charts" && window.AgentCanvas) { AgentCanvas.resizeCharts(); }
  }

  /** 顶栏标题：展示当前会话的问题（代替原视图名，视图名由 Tab 表达）。 */
  function setThreadTitle(text) {
    var el = document.getElementById("view-title");
    if (el) { el.textContent = text || "对话"; el.title = text || ""; }
  }

  // ---------------------------------------------------------------- 产物徽标
  function renderBadges(state) {
    var arts = state.currentArtifacts || {};
    Object.keys(ARTIFACT_BADGES).forEach(function (view) {
      var n = (arts[ARTIFACT_BADGES[view]] || []).length;
      var el = document.getElementById("badge-" + view);
      if (!el) { return; }
      el.classList.toggle("hidden", !n);
      el.textContent = n > 99 ? "99+" : String(n);
    });
  }

  /** Agent 等待用户输入时，在「对话」Tab 上挂提示圆点。 */
  function renderChatDot(state) {
    var dot = document.getElementById("nav-chat-dot");
    if (dot) { dot.classList.toggle("hidden", state.agentStatus !== "awaiting"); }
  }

  // ---------------------------------------------------------------- 线程存储
  function loadThreads() {
    var list;
    try { list = JSON.parse(localStorage.getItem(THREAD_KEY) || "[]"); } catch (e) { return []; }
    if (!Array.isArray(list)) { return []; }
    // 旧版数据无 id：按时间戳补一个稳定 id
    list.forEach(function (t) { if (!t.id) { t.id = "legacy-" + t.ts; } });
    return list;
  }

  /** 持久化列表；超配额时从最旧会话起逐个丢快照重试（保元信息，弃内容）。 */
  function saveThreads(list) {
    list = list.slice(0, MAX_THREADS);
    for (var guard = 0; guard <= list.length; guard++) {
      try {
        localStorage.setItem(THREAD_KEY, JSON.stringify(list));
        return;
      } catch (e) {
        var victim = -1;
        for (var j = list.length - 1; j >= 0; j--) {
          if (list[j].snapshot) { victim = j; break; }
        }
        if (victim < 0) { return; } // 无快照可弃仍失败：放弃持久化，内存态不受影响
        list[victim].snapshot = null;
      }
    }
  }

  function setActiveThread(id) {
    activeThreadId = id || "";
    try {
      if (activeThreadId) { localStorage.setItem(ACTIVE_KEY, activeThreadId); }
      else { localStorage.removeItem(ACTIVE_KEY); }
    } catch (e) { /* 忽略 */ }
  }

  /** 归档当前工作区到活跃会话（空工作区跳过）。流式中途调用也安全。 */
  function archiveActiveThread() {
    if (!activeThreadId) { return; }
    var st = AgentStore.get();
    if (!st.timelineEvents.length) { return; }
    var snap = {
      timelineEvents: st.timelineEvents,
      activePlan: st.activePlan,
      currentArtifacts: st.currentArtifacts,
      finalReport: st.finalReport
    };
    var list = loadThreads();
    var hit = false;
    list.forEach(function (t) {
      if (t.id === activeThreadId) {
        t.events = st.timelineEvents.length;
        t.snapshot = snap;
        hit = true;
      }
    });
    if (hit) { saveThreads(list); }
  }

  /** 防抖归档：工作区每次变更都调度，保证中途切会话/刷新不丢已完成部分。 */
  var archiveTimer = null;
  function scheduleArchive() {
    if (archiveTimer) { clearTimeout(archiveTimer); }
    archiveTimer = setTimeout(function () {
      archiveTimer = null;
      archiveActiveThread();
    }, ARCHIVE_DEBOUNCE_MS);
  }

  /** 新一轮提问：先归档旧会话，再登记新会话为活跃（产物与会话绑定）。 */
  function beginThread(q) {
    if (archiveTimer) { clearTimeout(archiveTimer); archiveTimer = null; }
    archiveActiveThread();
    var id = "t" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
    var list = loadThreads();
    list.unshift({ id: id, q: q, ts: Date.now(), events: 0, snapshot: null });
    setActiveThread(id);
    saveThreads(list);
    setThreadTitle(q);
    renderThreads();
  }

  /** 点击历史会话：归档当前 -> 载入该会话快照；无快照（旧数据/降级）回退回填输入框。 */
  function loadThread(id) {
    if (id === activeThreadId) { showView("chat"); return; }
    if (window.__activeStream) { window.__activeStream.abort(); }
    if (archiveTimer) { clearTimeout(archiveTimer); archiveTimer = null; }
    archiveActiveThread();
    var t = loadThreads().find(function (x) { return x.id === id; });
    if (!t) { return; }
    var input = document.getElementById("query");
    if (t.snapshot) {
      AgentStore.loadSnapshot(t.snapshot);
      setActiveThread(id);
      setThreadTitle(t.q);
      if (input) { input.value = ""; }
    } else {
      setActiveThread("");
      AgentStore.reset();
      setThreadTitle("对话");
      if (input) { input.value = t.q; input.focus(); }
    }
    showView("chat");
    renderThreads();
  }

  function removeThread(id) {
    var list = loadThreads().filter(function (t) { return t.id !== id; });
    saveThreads(list);
    if (id === activeThreadId) {
      setActiveThread("");
      AgentStore.reset();
      setThreadTitle("对话");
    }
    renderThreads();
  }

  // ---------------------------------------------------------------- 历史列表
  function renderThreads() {
    var body = document.getElementById("thread-list");
    var threads = loadThreads();
    if (!threads.length) {
      body.innerHTML = '<div class="hp-empty">暂无历史会话</div>';
      return;
    }
    body.innerHTML = "";
    threads.forEach(function (t) {
      var item = document.createElement("div");
      item.className = "thread-item" + (t.id === activeThreadId ? " active" : "");
      item.innerHTML = '<span class="t-q" title="' + esc(t.q) + '">' + esc(t.q) + "</span>"
        + '<span class="t-time">' + fmtTime(t.ts) + "</span>"
        + '<button type="button" class="t-del" title="删除该会话">✕</button>';
      item.querySelector(".t-q").addEventListener("click", function () { loadThread(t.id); });
      item.querySelector(".t-time").addEventListener("click", function () { loadThread(t.id); });
      item.querySelector(".t-del").addEventListener("click", function (e) {
        e.stopPropagation();
        removeThread(t.id);
      });
      body.appendChild(item);
    });
  }

  // ---------------------------------------------------------------- 知识目录（Schema）
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
          + '<span class="f-column">' + esc(f.column) + "</span>"
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

  function closeSchema() {
    document.getElementById("schema-panel").classList.add("hidden");
    document.getElementById("schema-btn").setAttribute("aria-expanded", "false");
  }

  // ---------------------------------------------------------------- 新对话
  function newThread() {
    if (window.__activeStream) { window.__activeStream.abort(); }
    if (archiveTimer) { clearTimeout(archiveTimer); archiveTimer = null; }
    archiveActiveThread(); // 旧对话连同产物归档进历史，不被清除
    AgentStore.reset();
    if (window.AgentCanvas) { AgentCanvas.reset(); }
    setActiveThread("");
    showView("chat");
    setThreadTitle("对话");
    var input = document.getElementById("query");
    input.value = "";
    input.focus();
    closeSchema();
    renderThreads();
  }

  /** 页面刷新恢复：活跃会话若有快照则原样载入（含四视图产物）。 */
  function restoreActiveThread() {
    var saved = "";
    try { saved = localStorage.getItem(ACTIVE_KEY) || ""; } catch (e) { /* 忽略 */ }
    if (!saved) { return; }
    var t = loadThreads().find(function (x) { return x.id === saved; });
    if (t && t.snapshot) {
      setActiveThread(saved);
      AgentStore.loadSnapshot(t.snapshot);
      setThreadTitle(t.q);
    } else {
      try { localStorage.removeItem(ACTIVE_KEY); } catch (e) { /* 忽略 */ }
    }
  }

  // ---------------------------------------------------------------- 入口
  function init() {
    // 视图切换（顶部 Tab 条）
    document.querySelectorAll(".view-tab").forEach(function (btn) {
      btn.addEventListener("click", function () { showView(btn.dataset.view); });
    });

    // 产物徽标 + 等待输入圆点
    AgentStore.subscribe("currentArtifacts", renderBadges);
    AgentStore.subscribe("agentStatus", renderChatDot);

    // 工作区变更即防抖归档（流式中途切会话/刷新也能保留已完成部分）
    AgentStore.subscribe("timelineEvents", scheduleArchive);
    AgentStore.subscribe("activePlan", scheduleArchive);

    // 知识目录弹出面板
    var schemaBtn = document.getElementById("schema-btn");
    schemaBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var panel = document.getElementById("schema-panel");
      var open = panel.classList.contains("hidden");
      panel.classList.toggle("hidden", !open);
      schemaBtn.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) { loadSchema(); }
    });
    document.addEventListener("click", function (e) {
      if (!e.target.closest(".side-pop-anchor")) { closeSchema(); }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { closeSchema(); }
    });

    document.getElementById("new-thread-btn").addEventListener("click", newThread);

    restoreActiveThread();
    renderThreads();
  }

  window.AgentSidebarUI = {
    init: init,
    showView: showView,
    beginThread: beginThread,
    archiveThread: archiveActiveThread,
    newThread: newThread
  };
})();
