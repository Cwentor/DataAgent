/* Agent 工作台集中状态 Store（pub/sub 模式，零依赖）。
 *
 * 管理四块状态：
 * - activePlan：当前任务 DAG（plan_created 建立 / plan 步骤状态随 tool 事件推进）；
 * - timelineEvents：左栏执行流时间线（append-only 事件列表）；
 * - currentArtifacts：右栏产物（markdown_report / echarts / table / code_snippet）；
 * - hitlState：HITL 澄清交互（question / options / resume_token / 已答复态）。
 *
 * 另管理会话运行时注册表（getRuntime）：
 * - 每会话一份流式运行时 {handle, runId, lastSeq, resumeToken, query}，
 *   切换会话不清理——后台 run 依赖它做游标重连与 HITL 恢复；
 *
 * 组件只通过 store.subscribe(部分字段, 回调) 感知变更，不直接互相引用。
 */
(function () {
  "use strict";

  var listeners = {}; // field -> [fn]

  var state = {
    /** 运行标志（驱动状态灯与提交按钮）。 */
    running: false,
    /** Agent 状态灯：idle | planning | executing | awaiting */
    agentStatus: "idle",
    /** 当前轮次（turn_id）。 */
    turnId: "",
    /** 快照代际号：每次整体替换状态（reset / loadSnapshot）自增，
     *  供对话流等渲染方感知「会话切换」并强制全量重建。 */
    generation: 0,
    /** 当前任务 DAG：[{id, title, kind, status}]。 */
    activePlan: [],
    /** 左栏时间线：[{kind, ...}]（kind: user|plan|tool|reflection|hitl|done|error）。 */
    timelineEvents: [],
    /** 轮次索引：本会话每轮提问的元信息 [{turn, text, ts}]（turn 从 1 递增）。
     *  产物按轮次绑定（artifact.turn），产物视图据此分组并标注来源轮次。 */
    turns: [],
    /** 当前轮号：pushUserMessage 开启新一轮时自增；产物入账即打上该轮号。 */
    currentTurn: 0,
    /** 右栏产物：{reports:[], charts:[], codes:[], tables:[]}；
     *  每条产物携带 turn / turnText 标记（哪一轮产出的什么产物）。 */
    currentArtifacts: { reports: [], charts: [], codes: [], tables: [] },
    /** 最终报告 markdown（导出用；取最新一轮报告）。 */
    finalReport: "",
    /** HITL 交互态。 */
    hitlState: null
  };

  function emit(field) {
    (listeners[field] || []).forEach(function (fn) {
      try { fn(state); } catch (e) { /* 单个订阅者异常不拖垮广播 */ }
    });
  }

  function emitItem(field, item) {
    (listeners[field] || []).forEach(function (fn) {
      try { fn(state, item); } catch (e) { /* 忽略 */ }
    });
  }

  /** 按轮号取提问原文（模块级：pushArtifact / turnInfo 共用，避免 self 引用）。 */
  function turnTextOf(s, turn) {
    if (!turn) { return ""; }
    var text = "";
    (s.turns || []).forEach(function (t) { if (t.turn === turn) { text = t.text || ""; } });
    return text;
  }

  // ------------------------------------------------------------ 会话运行时注册表
  // 每会话一份流式运行时（句柄/游标/resume_token/提问原文），与会话 id 绑定；
  // 切换会话不清理（后台 run 依赖它做游标重连），仅 removeThread 时删除。
  var runtimes = {}; // threadId -> {handle, runId, lastSeq, resumeToken, query}

  function runtimeOf(threadId) {
    if (!threadId) { threadId = "_blank"; }
    if (!runtimes[threadId]) {
      runtimes[threadId] = { handle: null, runId: "", lastSeq: 0, resumeToken: "", query: "" };
    }
    return runtimes[threadId];
  }

  var store = {
    get: function () { return state; },

    /** @param {string} field @param {(s: Object) => void} fn */
    subscribe: function (field, fn) {
      (listeners[field] = listeners[field] || []).push(fn);
      fn(state); // 立即回放当前值（简化组件初始化）
      return function () {
        listeners[field] = listeners[field].filter(function (x) { return x !== fn; });
      };
    },

    /** 会话流式运行时（不存在则初始化空壳；app.js / sidebar-ui 共享）。 */
    getRuntime: function (threadId) { return runtimeOf(threadId); },

    dropRuntime: function (threadId) { delete runtimes[threadId || "_blank"]; },

    reset: function () {
      state.activePlan = [];
      state.timelineEvents = [];
      state.turns = [];
      state.currentTurn = 0;
      state.currentArtifacts = { reports: [], charts: [], codes: [], tables: [] };
      state.finalReport = "";
      state.hitlState = null;
      state.turnId = "";
      state.running = false; // 新工作区从空闲开始（后台 run 不受影响）
      state.agentStatus = "idle";
      state.generation++;
      emit("generation");
      emit("activePlan"); emit("timelineEvents"); emit("currentArtifacts"); emit("hitlState");
      emit("turns"); emit("running"); emit("agentStatus");
    },

    /** 载入会话快照：整体替换状态并广播（时间线 uid 由快照原样带回）。
     *  @param {{timelineEvents?:Array, activePlan?:Array, turns?:Array,
     *           currentArtifacts?:Object, finalReport?:string}} snap */
    loadSnapshot: function (snap) {
      snap = snap || {};
      state.timelineEvents = (snap.timelineEvents || []).slice();
      state.activePlan = (snap.activePlan || []).slice();
      state.turns = (snap.turns || []).slice();
      state.currentTurn = snap.currentTurn || state.turns.length;
      var arts = snap.currentArtifacts || {};
      state.currentArtifacts = {
        reports: (arts.reports || []).slice(),
        charts: (arts.charts || []).slice(),
        codes: (arts.codes || []).slice(),
        tables: (arts.tables || []).slice()
      };
      state.finalReport = snap.finalReport || "";
      state.hitlState = null; // HITL 澄清不跨会话恢复
      state.turnId = "";
      state.running = false;
      state.agentStatus = "idle";
      state.generation++;
      emit("generation");
      emit("activePlan"); emit("timelineEvents"); emit("currentArtifacts");
      emit("turns"); emit("hitlState"); emit("running"); emit("agentStatus");
    },

    setRunning: function (v) {
      state.running = !!v;
      if (v) { state.agentStatus = "planning"; }
      else if (state.agentStatus !== "awaiting") { state.agentStatus = "idle"; }
      emit("running"); emit("agentStatus");
    },

    setAgentStatus: function (s) {
      state.agentStatus = s;
      emit("agentStatus");
    },

    setTurnId: function (id) {
      if (id && state.turnId !== id) { state.turnId = id; }
    },

    /** 用户提问入时间线，并开启新轮次（产物按轮次绑定）。 */
    pushUserMessage: function (text) {
      state.currentTurn += 1;
      state.turns.push({ turn: state.currentTurn, text: text, ts: Date.now() });
      state.timelineEvents.push({ kind: "user", text: text, turn: state.currentTurn });
      emit("turns");
      emit("timelineEvents");
    },

    /** 轮次元信息（产物分组标题用）；未知轮号返回兜底文案。 */
    turnInfo: function (turn) {
      var hit = null;
      state.turns.forEach(function (t) { if (t.turn === turn) { hit = t; } });
      return hit || { turn: turn || 0, text: "", ts: 0 };
    },
    /** 当前轮号（产物打标；0 = 无轮次语境，如历史快照遗留产物）。 */
    currentTurnNo: function () { return state.currentTurn; },

    /** plan_created：建立/替换 DAG。 */
    setPlan: function (plan) {
      state.activePlan = plan;
      emit("activePlan");
    },

    /** 步骤状态推进（query/analyze 步骤随工具事件同步）。 */
    setStepStatus: function (stepId, status) {
      var hit = false;
      state.activePlan.forEach(function (s) {
        if (s.id === stepId) { s.status = status; hit = true; }
      });
      if (hit) { emit("activePlan"); }
    },

    /** 时间线追加（plan 卡片只保留最新一张）。 */
    pushTimeline: function (item) {
      if (item.kind === "plan") {
        // 重规划时替换旧计划卡（时间线保留一个活计划）
        state.timelineEvents = state.timelineEvents.filter(function (e) { return e.kind !== "plan"; });
      }
      state.timelineEvents.push(item);
      emit("timelineEvents");
    },

    /** 当前运行中的工具块（tool_start 建、tool_end 补全）。
     * 匹配规则：从后往前找同 toolId 且未结束的块；tool_end 只覆盖结果字段。 */
    upsertToolEvent: function (toolEvent) {
      var t = state.timelineEvents;
      for (var i = t.length - 1; i >= 0; i--) {
        if (t[i].kind === "tool" && t[i].toolId === toolEvent.toolId && !t[i].ended) {
          // tool_end 增量合并：保留已渲染的 input/名称，只更新结果字段
          var target = t[i];
          Object.keys(toolEvent).forEach(function (k) {
            var v = toolEvent[k];
            if (k === "input") { return; } // input 以 tool_start 为准
            if (v !== null && v !== undefined && v !== "") { target[k] = v; }
          });
          target.ended = true;
          emit("timelineEvents");
          emitItem("toolUpdate", target);
          return;
        }
      }
      t.push(toolEvent);
      emit("timelineEvents");
    },

    /** 产物入画布：自动打上当前轮次标记（哪一轮产出的什么产物）。
     *  已带 turn 的产物（如重放补标 / 恢复快照）不覆盖。 */
    pushArtifact: function (artifact) {
      var map = { markdown_report: "reports", echarts: "charts", code_snippet: "codes", table: "tables" };
      var key = map[artifact.type];
      if (!key) { return; }
      if (artifact.turn == null) {
        artifact.turn = state.currentTurn;
        artifact.turnText = turnTextOf(state, state.currentTurn);
      }
      state.currentArtifacts[key].push(artifact);
      if (artifact.type === "markdown_report") { state.finalReport = artifact.content; }
      emit("currentArtifacts");
    },

    setHitl: function (hitl) {
      state.hitlState = hitl; // null = 清除
      if (hitl) { state.agentStatus = "awaiting"; }
      emit("hitlState"); emit("agentStatus");
    }
  };

  window.AgentStore = store;
})();
