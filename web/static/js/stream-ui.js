/* 左栏执行流渲染（AgentChatStream）。
 *
 * 渲染 store.timelineEvents 全量重绘为轻量 DOM（事件量级：几十条/轮，
 * 超过 60 条后仅保留最近 60 条的 DOM 节点，避免长会话内存与布局抖动）。
 * 自动滚动：贴底时跟随；用户上滚后暂停跟随，重新贴底恢复（无回弹抖动）。
 */
(function () {
  "use strict";

  var ICONS = { query: "⟳", analyze: "⚙", synthesize: "✎" };
  var PLAN_STATUS_ICON = { pending: "○", running: "◌", done: "●", failed: "✕" };
  var PLAN_STATUS_TEXT = { pending: "等待", running: "执行中", done: "完成", failed: "失败" };
  var TOOL_LABELS = AgentProtocol.TOOL_LABELS;
  var MAX_DOM_ITEMS = 60;

  var scrollBox, listBox;
  var stickBottom = true;

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function initScroll() {
    scrollBox = $("stream-scroll");
    listBox = $("stream");
    scrollBox.addEventListener("scroll", function () {
      var near = scrollBox.scrollHeight - scrollBox.scrollTop - scrollBox.clientHeight;
      stickBottom = near < 40; // 距底 40px 内视为贴底
    });
  }

  function autoScroll() {
    if (stickBottom) { scrollBox.scrollTop = scrollBox.scrollHeight; }
  }

  function pruneDom() {
    while (listBox.children.length > MAX_DOM_ITEMS) {
      listBox.removeChild(listBox.firstChild);
    }
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
    var stateHtml = "";
    if (item.ended) {
      stateHtml = item.error ? '<span class="tool-state fail">✕ 失败</span>'
        : '<span class="tool-state ok">✓ 完成</span>';
    }
    var durHtml = item.duration_ms != null ? '<span class="tool-dur">' + fmtDur(item.duration_ms) + "</span>" : "";
    head.innerHTML = '<span class="tool-badge ' + (item.name === "python_sandbox" ? "sandbox" : item.name === "metric_meta_lookup" ? "meta" : "dsl") + '">'
      + esc(badgeText) + "</span>" + stateHtml + durHtml
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

  // ------------------------------------------------------------ 全量重绘入口
  function render(state) {
    // 计划卡状态就地刷新（避免整卡重排）
    if (state.activePlan.length) { updatePlanCard(state.activePlan); }

    var items = state.timelineEvents;
    var rendered = listBox.querySelectorAll("[data-timeline-idx]");
    var renderedCount = rendered.length;

    if (items.length < renderedCount) {
      // 会话重置：全量重建
      listBox.innerHTML = "";
      renderedCount = 0;
    }

    // 移除旧的 thinking 指示
    var oldThinking = listBox.querySelector("[data-thinking]");
    if (oldThinking) { oldThinking.remove(); }

    for (var i = renderedCount; i < items.length; i++) {
      var item = items[i];
      var node = null;
      if (item.kind === "user") { node = elUserMessage(item); }
      else if (item.kind === "plan") { node = elPlanCard(item); }
      else if (item.kind === "tool") { node = elTool(item); }
      else if (item.kind === "reflection") { node = elReflection(item); }
      else if (item.kind === "hitl") { node = elHitl(item); }
      else if (item.kind === "done") { node = elDone(item); }
      else if (item.kind === "error") { node = elError(item); }
      if (node) {
        node.dataset.timelineIdx = String(i);
        listBox.appendChild(node);
      }
    }

    // 运行中显示 thinking 尾巴
    if (state.running) { listBox.appendChild(elThinking()); }

    pruneDom();
    autoScroll();
  }

  /** 就地更新工具块：状态/耗时/结果体（tool_end 增量合并后触发）。 */
  function updateToolBlock(_state, item) {
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
