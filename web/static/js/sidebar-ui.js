/* 左侧边栏 UI（AgentSidebarUI）。
 *
 * 职责：
 * - 视图切换（主区顶部 Tab 条）：「对话 / 执行报告 / 图表 / 代码沙箱 / 数据审计」
 *   五视图绑定当前会话，切换会话即切换整套产物；
 * - 会话历史：本机 localStorage 最近 20 条，每条绑定整轮快照 + 轮次 runId
 *   清单（t.turns）；点击历史会话恢复对应内容；
 * - 并行会话铁律：切换会话 / 新对话 **绝不 abort 任何流**——任务在服务端
 *   run 注册表继续执行并入缓冲；切回运行中会话按 t.turns 逐轮游标重放
 *   （最后一轮实时续传），缓冲过期（404/TTL）回退本地快照；
 * - 会话行状态徽标：对运行中/挂起的 run 以 5s 轮询
 *   GET /api/v1/agent/runs/<id> 驱动，终态即停（侧栏可见后台任务进展）；
 * - 快照防抖归档：工作区每次变更都调度，localStorage 超限从最旧丢快照；
 * - 新对话：归档当前会话快照后重置工作区（后台流不受影响）。
 */
(function () {
  "use strict";

  var THREAD_KEY = "dataagent_threads";
  var ACTIVE_KEY = "dataagent_active_thread";
  var MAX_THREADS = 20;
  var ARCHIVE_DEBOUNCE_MS = 800;
  var STATUS_POLL_MS = 5000;
  var STATUS_LABELS = {
    running: "分析中",
    paused: "待澄清",
    done: "",
    failed: "失败"
  };

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

  // 后台状态轮询注册表：runId -> timer（终态即停；切换页面不重复注册）
  var pollTimers = {};

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
      timelineEvents: JSON.parse(JSON.stringify(st.timelineEvents)),
      activePlan: JSON.parse(JSON.stringify(st.activePlan)),
      // 轮次索引随快照留存（产物分组与「第 N 轮」标注依赖它）
      turns: JSON.parse(JSON.stringify(st.turns || [])),
      currentTurn: st.currentTurn || 0,
      // 产物含 turn/turnText 标记（哪一轮产出的什么产物），一并持久化
      currentArtifacts: JSON.parse(JSON.stringify(st.currentArtifacts)),
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

  /**
   * 新一轮提问（仅空白工作台首次调用）：登记新会话为活跃。
   * 返回新会话 id（app.js 据此绑定事件路由）。
   */
  function beginThread(q) {
    if (archiveTimer) { clearTimeout(archiveTimer); archiveTimer = null; }
    archiveActiveThread();
    var id = "t" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
    var list = loadThreads();
    list.unshift({ id: id, q: q, ts: Date.now(), events: 0, turns: [], turnQueries: [], snapshot: null });
    setActiveThread(id);
    saveThreads(list);
    setThreadTitle(q);
    renderThreads();
    return id;
  }

  /** 登记会话的服务端 run（提问 / 恢复成功后调用，驱动重放与轮询）。
   *  q：本轮提问原文（重放时重建该轮用户气泡与产物轮次标注）。 */
  function attachRun(threadId, runId, q) {
    if (!threadId || !runId) { return; }
    var list = loadThreads();
    list.forEach(function (t) {
      if (t.id === threadId) {
        t.turns = t.turns || [];
        if (t.turns.indexOf(runId) === -1) {
          t.turns.push(runId);
          // 平行数组：第 i 个 runId 对应的提问原文（与 turns 索引对齐）
          t.turnQueries = t.turnQueries || [];
          t.turnQueries.push(q || t.q || "");
        }
      }
    });
    saveThreads(list);
    startStatusPoll(runId);
    renderThreads();
  }

  // ---------------------------------------------------------------- run 状态轮询（侧栏徽标）
  function authFetch(url) {
    var headers = {};
    var token = sessionStorage.getItem("dataagent_token") || localStorage.getItem("dataagent_token") || "";
    if (token) { headers.Authorization = "Bearer " + token; }
    var sid = sessionStorage.getItem("dataagent_sid") || localStorage.getItem("dataagent_session") || "";
    if (sid) { headers["X-Session-ID"] = sid; }
    return fetch(url, { headers: headers });
  }

  function startStatusPoll(runId) {
    if (!runId || pollTimers[runId]) { return; }
    var tick = function () {
      authFetch("/api/v1/agent/runs/" + encodeURIComponent(runId))
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (snap) {
          if (!snap) { stopStatusPoll(runId); renderThreads(); return; } // 404：缓冲过期
          if (snap.status === "done" || snap.status === "failed") {
            stopStatusPoll(runId);
            if (snap.status === "failed") { toast("后台任务失败：" + (snap.error || "未知错误"), "err"); }
          }
          renderThreads();
        })
        .catch(function () { /* 网络抖动：下一轮再试 */ });
    };
    pollTimers[runId] = setInterval(tick, STATUS_POLL_MS);
    tick();
  }

  function stopStatusPoll(runId) {
    if (pollTimers[runId]) { clearInterval(pollTimers[runId]); delete pollTimers[runId]; }
  }

  function stopAllPolls() {
    Object.keys(pollTimers).forEach(stopStatusPoll);
  }

  /** 各会话的后台状态快照缓存（renderThreads 渲染用；轮询 tick 刷新）。 */
  var runStatusCache = {};

  // ---------------------------------------------------------------- 会话重放恢复
  /**
   * 重放恢复会话：按 t.turns 逐轮游标订阅（after=0 全量重放历史轮已完成
   * 缓冲——done 轮重放即得完整事件序列；最后一轮未终态则实时续传）。
   * 缓冲过期（404 / error 帧含"溢出"）-> 回退本地快照。
   *
   * 轮次重建：每轮重放前先 pushUserMessage(该轮提问)——既补回用户气泡，
   * 又开启对应轮号，使重放产出的产物自动带上正确的 turn 标记（产物分组依据）。
   * 事件回调与 app.js handleAgentEvent 复用同一入口（AgentApp.onThreadEvent）。
   */
  function replayThread(t) {
    AgentStore.reset();
    AgentStreamUI.resetScroll();
    AgentCanvas.reset();
    var turns = (t.turns || []).filter(Boolean);
    if (!turns.length || !window.AgentApp) { return Promise.resolve(false); }
    var queries = t.turnQueries || [];
    var seq = 0;
    var chain = Promise.resolve(true);
    turns.forEach(function (runId, i) {
      chain = chain.then(function (ok) {
        if (!ok) { return false; } // 前轮失败：后续轮事件缺前置语境，直接回退快照
        return new Promise(function (resolve) {
          var started = false;
          var handle = AgentEventSource.open(
            AgentProtocol.buildStreamUrl({ run_id: runId, after: 0 }),
            {
              reconnect: false,
              onEvent: function (ev) {
                if (ev.seq && ev.seq > seq) { seq = ev.seq; }
                // 该轮首帧前开启轮次（用户气泡 + 轮号），产物据此打标
                if (!started) {
                  started = true;
                  AgentStore.pushUserMessage(queries[i] || t.q || "");
                }
                if (ev.event === "__stream_end__") {
                  resolve(true);
                  return;
                }
                if (ev.event === "error") {
                  resolve(false);
                  return;
                }
                AgentApp.onThreadEvent(t.id, ev);
              },
              onError: function () { resolve(false); }
            }
          );
          handle.done.then(function () { resolve(true); });
        });
      });
    });
    return chain.then(function (ok) {
      if (!ok) { return false; }
      // 恢复轮次的原提问进运行时（HITL 答复需原文）
      var rt = AgentStore.getRuntime(t.id);
      rt.query = queries[queries.length - 1] || t.q || "";
      rt.runId = turns[turns.length - 1];
      rt.lastSeq = seq;
      return true;
    });
  }

  /** 快照兜底渲染（重放不可用 / 无 turns 时的既有行为）。 */
  function loadFromSnapshot(t) {
    var input = document.getElementById("query");
    if (t.snapshot) {
      AgentStore.loadSnapshot(t.snapshot);
      if (input) { input.value = ""; }
    } else {
      AgentStore.reset();
      if (input) { input.value = t.q; input.focus(); }
    }
  }

  /**
   * 点击历史会话：归档当前 -> 重放恢复（运行中/有缓冲）或快照兜底。
   * 严禁 abort 任何流：当前会话若在跑，其读取器保持、事件改投后台
   * （handleAgentEvent 按活跃会话路由）；本会话后续切回可再次重放续传。
   */
  function loadThread(id) {
    if (id === activeThreadId) { showView("chat"); return; }
    if (archiveTimer) { clearTimeout(archiveTimer); archiveTimer = null; }
    archiveActiveThread();
    var t = loadThreads().find(function (x) { return x.id === id; });
    if (!t) { return; }
    setActiveThread(id);
    setThreadTitle(t.q);
    var input = document.getElementById("query");
    if (input) { input.value = ""; }
    replayThread(t).then(function (ok) {
      if (!ok) { loadFromSnapshot(t); }
      else { AgentStreamUI.resetScroll(); }
      showView("chat");
      renderThreads();
    });
    renderThreads();
  }

  function removeThread(id) {
    var target = loadThreads().find(function (x) { return x.id === id; });
    (target && target.turns || []).forEach(stopStatusPoll);
    var list = loadThreads().filter(function (t) { return t.id !== id; });
    saveThreads(list);
    AgentStore.dropRuntime(id);
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
      var badge = threadStatusBadge(t);
      item.innerHTML = '<span class="t-q" title="' + esc(t.q) + '">' + esc(t.q) + "</span>"
        + '<span class="t-time">' + fmtTime(t.ts) + "</span>"
        + badge
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

  /** 会话行状态徽标：活跃会话看 Store；其余看运行中 run 的轮询缓存。 */
  function threadStatusBadge(t) {
    var text = "";
    if (t.id === activeThreadId) {
      var st = AgentStore.get();
      if (st.running) { text = "分析中"; }
      else if (st.agentStatus === "awaiting") { text = "待澄清"; }
    } else if ((t.turns || []).length) {
      var runId = t.turns[t.turns.length - 1];
      var cached = runStatusCache[runId];
      if (cached && STATUS_LABELS[cached]) { text = STATUS_LABELS[cached]; }
    }
    return text
      ? '<span class="t-status" data-status="' + text + '">' + text + "</span>"
      : "";
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
  /** 归档当前会话后重置工作区；后台流不受影响（服务端继续执行，可切回重放）。 */
  function newThread() {
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

  /** 页面刷新恢复：优先快照渲染；运行中会话的 run 注册轮询（徽标续跟踪）。 */
  function restoreActiveThread() {
    var saved = "";
    try { saved = localStorage.getItem(ACTIVE_KEY) || ""; } catch (e) { /* 忽略 */ }
    var threads = loadThreads();
    // 所有会话的未终态 run 重新纳入轮询（服务端 run 可能在刷新期间完成）
    threads.forEach(function (t) {
      (t.turns || []).forEach(function (runId) {
        if (runId) { startStatusPoll(runId); }
      });
    });
    if (!saved) { return; }
    var t = threads.find(function (x) { return x.id === saved; });
    if (t) {
      setActiveThread(saved);
      if (t.snapshot) {
        AgentStore.loadSnapshot(t.snapshot);
        setThreadTitle(t.q);
      }
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
    newThread: newThread,
    /** 当前活跃会话 id（事件路由判定用）。 */
    activeThreadId: function () { return activeThreadId; },
    /** 登记会话的服务端 runId（重放清单 + 状态轮询）。 */
    attachRun: attachRun,
    /** 轮询快照入缓存（app.js 轮询回调也可驱动）。 */
    noteRunStatus: function (runId, status) {
      runStatusCache[runId] = status;
      renderThreads();
    }
  };
})();
