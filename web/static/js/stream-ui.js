/* 左栏执行流渲染（AgentChatStream，窗口化虚拟渲染）。
 *
 * 数据模型：store.timelineEvents 全量保留（不丢任何事件）；
 * DOM 模型：超过 VIRTUALIZE_THRESHOLD 条后仅渲染可视窗口 ±BUFFER 的条目，
 * 窗口外用上下 spacer 撑起滚动高度（高度 = 实测缓存 itemH[i] 或估算值），
 * 滚动时 rAF 节流滑动窗口。≤阈值时保持简单追加渲染（零虚拟化开销）。
 */
(function () {
  "use strict";

  var ICONS = { query: "⟳", analyze: "⚙", synthesize: "✎" };
  var PLAN_STATUS_ICON = { pending: "○", running: "◌", done: "●", failed: "✕" };
  var PLAN_STATUS_TEXT = { pending: "等待", running: "执行中", done: "完成", failed: "失败" };
  var TOOL_LABELS = AgentProtocol.TOOL_LABELS;
  var VIRTUALIZE_THRESHOLD = 50; // 超过该事件数后启用窗口化（规格：>50 步虚拟化）
  var BUFFER = 6;                // 视口上下各多渲染的条目数
  var EST_H = { user: 44, plan: 130, tool: 46, reflection: 64, hitl: 150, done: 52, error: 44 };

  var scrollBox, listBox;
  var spacerTop, spacerBottom;
  var stickBottom = true;
  var itemH = [];        // index -> 实测高度（null = 未渲染，用估算）
  var windowStart = 0;   // 当前窗口起点
  var windowEnd = -1;    // 当前窗口终点（不含）
  var rafPending = false;

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function initScroll() {
    scrollBox = $("stream-scroll");
    listBox = $("stream");
    // 虚拟化结构：上下 spacer + 窗口内容
    spacerTop = document.createElement("div");
    spacerTop.className = "virtual-spacer";
    spacerBottom = document.createElement("div");
    spacerBottom.className = "virtual-spacer";
    listBox.insertBefore(spacerTop, listBox.firstChild);
    listBox.appendChild(spacerBottom);
    scrollBox.addEventListener("scroll", function () {
      var near = scrollBox.scrollHeight - scrollBox.scrollTop - scrollBox.clientHeight;
      stickBottom = near < 40; // 距底 40px 内视为贴底
      scheduleWindow();
    });
    window.addEventListener("resize", scheduleWindow);
  }

  function autoScroll() {
    if (stickBottom) { scrollBox.scrollTop = scrollBox.scrollHeight; }
  }

  /** rAF 节流的窗口滑动（scroll/resize 高频触发）。 */
  function scheduleWindow() {
    if (rafPending) { return; }
    rafPending = true;
    requestAnimationFrame(function () {
      rafPending = false;
      renderWindow();
    });
  }

  // ------------------------------------------------------------ 高度模型
  function estHeight(items, i) {
    if (itemH[i]) { return itemH[i]; }
    return EST_H[items[i].kind] || 56;
  }

  function offsets(items) {
    var prefix = [0];
    for (var i = 0; i < items.length; i++) { prefix.push(prefix[i] + estHeight(items, i)); }
    return prefix;
  }

  function measureNode(node, idx) {
    if (node && node.offsetHeight) { itemH[idx] = node.offsetHeight; }
  }

  // ------------------------------------------------------------ 各类型节点渲染
  function elUserMessage(item) {
    var div = document.createElement("div");
    div.className = "msg-user";
    div.textContent = item.text;
    return div;
  }

  function elPlanCard(item) {
    var card = document.createElement("div");
    card.className = "stream-item plan-card";
    card.dataset.planCard = "1";
    card.innerHTML = '<div class="plan-title">任务计划（DAG）</div>';
    var stepsBox = document.createElement("div");
    stepsBox.className = "plan-steps";
    card.appendChild(stepsBox);
    fillPlanSteps(stepsBox, item.plan);
    return card;
  }

  function fillPlanSteps(stepsBox, plan) {
    stepsBox.innerHTML = "";
    (plan || []).forEach(function (s) {
      var row = document.createElement("div");
      row.className = "plan-step";
      row.dataset.status = s.status;
      row.dataset.stepId = s.id;
      row.innerHTML = '<span class="p-icon">' + (PLAN_STATUS_ICON[s.status] || "○") + "</span>"
        + '<span class="p-goal">' + esc(s.title) + "</span>"
        + '<span class="p-status">' + (PLAN_STATUS_TEXT[s.status] || s.status) + "</span>";
      stepsBox.appendChild(row);
    });
  }

  /** 就地更新计划卡步骤状态（复用 DOM，不重绘整卡）。 */
  function updatePlanCard(plan) {
    var card = listBox.querySelector('[data-plan-card]');
    if (!card) { return; }
    fillPlanSteps(card.querySelector(".plan-steps"), plan);
  }

  function elTool(item) {
    var acc = document.createElement("div");
    acc.className = "stream-item tool-acc";
    acc.dataset.toolId = item.toolId;
    acc.dataset.name = item.name;
    if (item.ended) { acc.dataset.ended = "1"; }
    acc.__input = item.input || null; // 缓存 input 供 tool_end 合并渲染
    var label = TOOL_LABELS[item.name] || item.name;
    var badgeText = item.name === "futurebi_dsl_query"
      ? (item.metric || label)
      : label;
    var head = document.createElement("button");
    head.type = "button";
    head.className = "tool-head";
    // 状态/耗时锚点常驻（data-role 供 tool_end 就地更新；未结束时留空）
    head.innerHTML = '<span class="tool-badge ' + (item.name === "python_sandbox" ? "sandbox" : item.name === "metric_meta_lookup" ? "meta" : "dsl") + '">'
      + esc(badgeText) + "</span>"
      + '<span data-role="state" class="tool-state">' + (item.ended ? (item.error ? "✕ 失败" : "✓ 完成") : "") + "</span>"
      + '<span data-role="dur" class="tool-dur">' + (item.duration_ms != null ? fmtDur(item.duration_ms) : "") + "</span>"
      + '<span class="tool-chevron">▾</span>';
    var body = document.createElement("div");
    body.className = "tool-body";
    head.addEventListener("click", function () { acc.classList.toggle("open"); });
    acc.appendChild(head);
    acc.appendChild(body);
    fillToolBody(body, item);
    return acc;
  }

  function fillToolBody(body, item) {
    body.innerHTML = "";
    if (item.name === "futurebi_dsl_query" && item.input && item.input.dsl) {
      body.innerHTML += "<h4>DSL（AST 契约）</h4><pre data-lang='json'>"
        + esc(JSON.stringify(item.input.dsl, null, 2)) + "</pre>";
    }
    if (item.name === "python_sandbox" && item.input && item.input.code) {
      body.innerHTML += "<h4>Python 分析脚本</h4><pre><code class='language-python'>"
        + esc(item.input.code) + "</code></pre>";
    }
    if (item.output) {
      if (item.name === "python_sandbox" && item.input && item.input.code) {
        body.innerHTML += "<h4>执行结果</h4><pre>" + esc(JSON.stringify(item.output, null, 2)) + "</pre>";
      } else {
        body.innerHTML += "<h4>执行结果</h4><pre>" + esc(JSON.stringify(item.output, null, 2)) + "</pre>";
      }
    }
    if (item.error) {
      body.innerHTML += '<div class="tool-err">' + esc(item.error) + "</div>";
    }
    if (window.Prism) { Prism.highlightAllUnder(body); }
  }

  function elReflection(item) {
    var div = document.createElement("div");
    div.className = "stream-item reflection-card";
    var r = item.reflection || {};
    div.innerHTML = '<span class="r-decision ' + esc(r.decision) + '">'
      + ({ proceed: "✓ 检查通过", retry: "↻ 自愈重试", replan: "⟳ 重规划" }[r.decision] || esc(r.decision))
      + "</span>" + esc(r.observation || "")
      + (r.reason ? '<div class="r-reason">' + esc(r.reason) + "</div>" : "");
    return div;
  }

  function elDone(item) {
    var div = document.createElement("div");
    div.className = "stream-item reflection-card";
    div.style.borderLeftColor = "#12b886";
    div.innerHTML = '<span class="r-decision proceed">✓ 分析完成</span>'
      + '<div class="r-reason">报告与产物已生成，见右侧画布。</div>';
    return div;
  }

  function elHitl(item) {
    var card = document.createElement("div");
    card.className = "stream-item hitl-card";
    card.innerHTML = '<div class="hitl-q">' + esc(item.question || "需要补充信息") + "</div>";
    var opts = item.options || [];
    if (opts.length) {
      var row = document.createElement("div");
      row.className = "hitl-options";
      opts.forEach(function (o) {
        var pill = document.createElement("button");
        pill.type = "button";
        pill.className = "hitl-pill";
        pill.dataset.value = o;
        pill.textContent = o;
        row.appendChild(pill);
      });
      card.appendChild(row);
    }
    // 自由输入兜底（无预置选项或选项都不匹配时使用）
    var inputRow = document.createElement("div");
    inputRow.className = "hitl-input-row";
    inputRow.innerHTML = '<input class="hitl-input" placeholder="或输入自定义答复…">'
      + '<button type="button" class="hitl-pill hitl-send">发送</button>';
    card.appendChild(inputRow);
    return card;
  }

  function elError(item) {
    var div = document.createElement("div");
    div.className = "stream-item tool-err";
    div.style.margin = "0";
    div.textContent = "执行出错：" + (item.error || "未知错误");
    return div;
  }

  function elThinking() {
    var div = document.createElement("div");
    div.className = "stream-thinking";
    div.dataset.thinking = "1";
    div.innerHTML = '<span>Agent 正在执行</span><span class="dots"></span>';
    return div;
  }

  // ------------------------------------------------------------ 窗口化虚拟渲染
  function elItemFor(item) {
    if (item.kind === "user") { return elUserMessage(item); }
    if (item.kind === "plan") { return elPlanCard(item); }
    if (item.kind === "tool") { return elTool(item); }
    if (item.kind === "reflection") { return elReflection(item); }
    if (item.kind === "hitl") { return elHitl(item); }
    if (item.kind === "done") { return elDone(item); }
    if (item.kind === "error") { return elError(item); }
    return null;
  }

  /** 计算可视窗口边界（前缀和二分 + BUFFER 外扩，钳制到 [0, n]）。 */
  function windowBounds(items, prefix) {
    var viewportH = scrollBox.clientHeight || 600;
    var top = Math.max(scrollBox.scrollTop, 0);
    var bottom = top + viewportH;
    // 二分找第一个 offset > top 的条目
    var lo = 0, hi = items.length;
    while (lo < hi) {
      var mid = (lo + hi) >> 1;
      if (prefix[mid + 1] <= top) { lo = mid + 1; } else { hi = mid; }
    }
    var start = Math.max(lo - BUFFER, 0);
    var end = start;
    while (end < items.length && prefix[end] < bottom) { end++; }
    return { start: start, end: Math.min(end + BUFFER, items.length) };
  }

  /** 渲染当前窗口：移除窗外 DOM、补齐窗内 DOM、更新 spacer。 */
  function renderWindow() {
    var items = AgentStore.get().timelineEvents;
    if (items.length <= VIRTUALIZE_THRESHOLD) { return; } // 非虚拟化模式
    var prefix = offsets(items);
    var w = windowBounds(items, prefix);
    windowStart = w.start;
    windowEnd = w.end;

    // 移除窗外节点
    var rendered = listBox.querySelectorAll("[data-timeline-idx]");
    for (var k = 0; k < rendered.length; k++) {
      var idx = Number(rendered[k].dataset.timelineIdx);
      if (idx < w.start || idx >= w.end) { rendered[k].remove(); }
    }
    // 补齐窗内节点（按序插入：SpacerTop 后、SpacerBottom 前）
    for (var i = w.start; i < w.end; i++) {
      if (listBox.querySelector('[data-timeline-idx="' + i + '"]')) { continue; }
      var node = elItemFor(items[i]);
      if (!node) { continue; }
      node.dataset.timelineIdx = String(i);
      listBox.insertBefore(node, spacerBottom);
      measureNode(node, i);
    }
    spacerTop.style.height = prefix[w.start] + "px";
    spacerBottom.style.height = (prefix[items.length] - prefix[w.end]) + "px";
  }

  /** 全量渲染入口：≤阈值走简单追加；>阈值走窗口化（数据永不丢弃）。 */
  function render(state) {
    // 计划卡状态就地刷新（避免整卡重排）
    if (state.activePlan.length) { updatePlanCard(state.activePlan); }

    var items = state.timelineEvents;
    if (items.length < itemH.length) {
      // 会话重置：全量重建（含高度缓存与窗口状态）
      listBox.innerHTML = "";
      listBox.appendChild(spacerTop);
      listBox.appendChild(spacerBottom);
      itemH = [];
      windowStart = 0;
      windowEnd = -1;
    }

    // 移除旧的 thinking 指示
    var oldThinking = listBox.querySelector("[data-thinking]");
    if (oldThinking) { oldThinking.remove(); }

    if (items.length > VIRTUALIZE_THRESHOLD) {
      // 虚拟化模式：贴底时先滚到末尾坐标，再渲染当前窗口
      renderWindow();
      if (state.running) { listBox.appendChild(elThinking()); }
      autoScroll();
      return;
    }

    // 简单模式：尾部增量追加（高度缓存同步维护，避免模式切换时错位）
    for (var i = itemH.length; i < items.length; i++) {
      var node = elItemFor(items[i]);
      if (node) {
        node.dataset.timelineIdx = String(i);
        listBox.insertBefore(node, spacerBottom);
        measureNode(node, i);
      } else {
        itemH[i] = EST_H.error; // 未知类型占位高度
      }
    }
    if (itemH.length > items.length) { itemH.length = items.length; }

    // 运行中显示 thinking 尾巴
    if (state.running) { listBox.appendChild(elThinking()); }

    autoScroll();
  }

  /** 就地更新工具块：状态/耗时/结果体（tool_end 增量合并后触发）。
   * 注：subscribe 回放只传 state（无 item），此处须防御空 item。 */
  function updateToolBlock(_state, item) {
    if (!item || !item.toolId) { return; }
    var target = listBox.querySelector('[data-tool-id="' + cssEscape(item.toolId) + '"]:not([data-ended])');
    if (!target) { return; }
    target.dataset.ended = "1";
    var stateEl = target.querySelector('[data-role="state"]');
    var durEl = target.querySelector('[data-role="dur"]');
    if (stateEl) {
      stateEl.textContent = item.error ? "✕ 失败" : "✓ 完成";
      stateEl.className = "tool-state " + (item.error ? "fail" : "ok");
    }
    if (durEl && item.duration_ms != null) { durEl.textContent = fmtDur(item.duration_ms); }
    if (item.output || item.error) {
      fillToolBody(target.querySelector(".tool-body"), {
        name: item.name || target.dataset.name || "",
        input: target.__input || null,
        output: item.output, error: item.error
      });
    }
  }

  function cssEscape(s) {
    return (window.CSS && CSS.escape) ? CSS.escape(s) : s.replace(/["\\:]/g, "\\$&");
  }

  function fmtDur(ms) {
    var n = Number(ms) || 0;
    return n >= 1000 ? (n / 1000).toFixed(1) + " s" : Math.round(n) + " ms";
  }

  window.AgentStreamUI = {
    init: function () {
      initScroll();
      AgentStore.subscribe("timelineEvents", render);
      AgentStore.subscribe("toolUpdate", updateToolBlock);
      AgentStore.subscribe("activePlan", function (state) {
        if (state.activePlan.length) { updatePlanCard(state.activePlan); }
      });
      AgentStore.subscribe("running", render);
    },
    resetScroll: function () { stickBottom = true; }
  };
})();
