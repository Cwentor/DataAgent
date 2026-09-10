/* AgentStreamEvent 流式协议契约（与后端 core/orchestrator/events.py 一一对应）。
 *
 * 协议：POST 语义走 GET /api/v1/agent/chat/stream（EventSource 兼容），
 * 每帧 `data: <AgentStreamEvent JSON>`；事件类型与载荷结构在此集中定义，
 * 前端所有消费方（store/stream/组件）仅依赖本文件的工厂与守卫函数。
 *
 * @typedef {Object} AgentStreamEvent
 * @property {string} turn_id           会话轮次标识（一次编排全局唯一）
 * @property {number} timestamp         服务端毫秒时间戳
 * @property {AgentEventType} event     事件类型
 * @property {Object} payload           事件载荷（按 event 类型判别）
 *
 * @typedef {'plan_created'|'step_start'|'tool_start'|'tool_end'|'reflection'|
 *           'hitl_request'|'artifact_emit'|'done'|'error'} AgentEventType
 */
(function () {
  "use strict";

  /** 事件类型全集（运行时守卫用；未知类型忽略不炸）。 */
  var AGENT_EVENT_TYPES = [
    "plan_created", "step_start", "tool_start", "tool_end",
    "reflection", "hitl_request", "artifact_emit", "done", "error"
  ];

  /** 工具名全集（与后端埋点对齐）。 */
  var TOOL_NAMES = ["futurebi_dsl_query", "python_sandbox", "metric_meta_lookup"];

  /** 工具名 -> 左栏徽标文案。 */
  var TOOL_LABELS = {
    futurebi_dsl_query: "DSL Query",
    python_sandbox: "Code Execution (Python)",
    metric_meta_lookup: "Metric Metadata"
  };

  /** 事件类型 -> Agent 状态灯（idle/planning/executing/awaiting）。 */
  var EVENT_STATUS = {
    plan_created: "planning",
    step_start: "executing",
    tool_start: "executing",
    tool_end: "executing",
    reflection: "planning",
    artifact_emit: "executing",
    hitl_request: "awaiting",
    done: "idle",
    error: "idle"
  };

  /**
   * 解析一帧 SSE data 文本为 AgentStreamEvent；非法帧返回 null（不炸流）。
   * @param {string} raw
   * @returns {AgentStreamEvent|null}
   */
  function parseEvent(raw) {
    try {
      var obj = JSON.parse(raw);
      if (!obj || typeof obj !== "object") { return null; }
      if (AGENT_EVENT_TYPES.indexOf(obj.event) === -1) { return null; }
      if (!obj.payload || typeof obj.payload !== "object") { obj.payload = {}; }
      obj.turn_id = obj.turn_id || "";
      obj.timestamp = obj.timestamp || 0;
      return obj;
    } catch (e) {
      return null;
    }
  }

  /**
   * 归一化 plan 数组（plan_created 载荷）。
   * @param {Array<{id:string,title:string,kind:string,status:string}>|undefined} plan
   */
  function normalizePlan(plan) {
    if (!Array.isArray(plan)) { return []; }
    return plan.map(function (s) {
      return {
        id: String(s.id || ""),
        title: String(s.title || s.goal || ""),
        kind: s.kind || "query",
        status: (["pending", "running", "done", "failed"].indexOf(s.status) >= 0) ? s.status : "pending"
      };
    });
  }

  /** 构建查询流 URL（含鉴权外的明文参数；鉴权走请求头）。 */
  function buildStreamUrl(query, extra) {
    var params = new URLSearchParams();
    params.set("query", query);
    if (extra && extra.human_reply) { params.set("human_reply", extra.human_reply); }
    if (extra && extra.resume_token) { params.set("resume_token", extra.resume_token); }
    if (extra && extra.provider_id) { params.set("provider_id", extra.provider_id); }
    if (extra && extra.model_id) { params.set("model_id", extra.model_id); }
    return "/api/v1/agent/chat/stream?" + params.toString();
  }

  window.AgentProtocol = {
    AGENT_EVENT_TYPES: AGENT_EVENT_TYPES,
    TOOL_NAMES: TOOL_NAMES,
    TOOL_LABELS: TOOL_LABELS,
    EVENT_STATUS: EVENT_STATUS,
    parseEvent: parseEvent,
    normalizePlan: normalizePlan,
    buildStreamUrl: buildStreamUrl
  };
})();
