/* Agent 工作台集中状态 Store（pub/sub 模式，零依赖）。
 *
 * 管理四块状态：
 * - activePlan：当前任务 DAG（plan_created 建立 / plan 步骤状态随 tool 事件推进）；
 * - timelineEvents：左栏执行流时间线（append-only 事件列表）；
 * - currentArtifacts：右栏产物（markdown_report / echarts / table / code_snippet）；
 * - hitlState：HITL 澄清交互（question / options / resume_token / 已答复态）。
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
    /** 当前任务 DAG：[{id, title, kind, status}]。 */
    activePlan: [],
    /** 左栏时间线：[{kind, ...}]（kind: user|plan|tool|reflection|hitl|done|error）。 */
    timelineEvents: [],
    /** 右栏产物：{reports:[], charts:[], codes:[], tables:[]}。 */
    currentArtifacts: { reports: [], charts: [], codes: [], tables: [] },
    /** 最终报告 markdown（导出用）。 */
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

    reset: function () {
      state.activePlan = [];
      state.timelineEvents = [];
      state.currentArtifacts = { reports: [], charts: [], codes: [], tables: [] };
      state.finalReport = "";
      state.hitlState = null;
      state.turnId = "";
      emit("activePlan"); emit("timelineEvents"); emit("currentArtifacts"); emit("hitlState");
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

    /** 用户提问入时间线。 */
    pushUserMessage: function (text) {
      state.timelineEvents.push({ kind: "user", text: text });
      emit("timelineEvents");
    },

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

    /** 产物入画布。 */
    pushArtifact: function (artifact) {
      var map = { markdown_report: "reports", echarts: "charts", code_snippet: "codes", table: "tables" };
      var key = map[artifact.type];
      if (!key) { return; }
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
