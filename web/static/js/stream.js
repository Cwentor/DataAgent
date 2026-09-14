/* SSE 事件流客户端（AgentEventSource）。
 *
 * 为什么不用原生 EventSource：浏览器原生实现只支持 GET 且无法注入
 * Authorization 头（本项目鉴权走 Bearer JWT / X-Session-ID），因此用
 * fetch + ReadableStream 手写解析（帧协议与 EventSource data: 一致）。
 *
 * 职责：
 * - 建立 / 终止流连接（AbortController）；
 * - 帧解析 -> AgentProtocol.parseEvent -> 事件分发（onEvent 回调）；
 * - 自动重连：网络中断（非服务端正常收尾）时指数退避重试；
 * - 401 统一交给全局登出（与 app.js 的 redirectToLogin 契约一致）。
 */
(function () {
  "use strict";

  var TOKEN_KEY = "dataagent_token";
  var SESSION_KEY = "dataagent_session";
  var SID_KEY = "dataagent_sid";

  function localGet(k) { try { return localStorage.getItem(k) || ""; } catch (e) { return ""; } }
  function sessionGet(k) { try { return sessionStorage.getItem(k) || ""; } catch (e) { return ""; } }
  function getToken() { return sessionGet(TOKEN_KEY) || localGet(TOKEN_KEY); }
  function getSid() { return sessionGet(SID_KEY) || localGet(SESSION_KEY) || ""; }

  /**
   * @typedef {Object} StreamHandle
   * @property {() => void} abort   终止本地读取（服务端 run 不受影响，可重连重放）
   * @property {Promise<void>} done 流结束（含 abort / 正常收尾 / 失败）
   */

  var agentEventSource = {
    /**
     * 打开一次 SSE 流。
     * @param {string} url 流地址（AgentProtocol.buildStreamUrl 生成）
     * @param {Object} opts
     * @param {(ev: AgentStreamEvent) => void} opts.onEvent 结构化事件回调
     * @param {(err: Error) => void} [opts.onError] 传输层错误（区别于 error 事件）
     * @param {boolean} [opts.reconnect] 断线自动重连（默认 true，最多 3 次）
     * @param {(runId: string) => void} [opts.onOpen] 响应头到达（携 X-Run-Id）
     * @param {(lastSeq: number) => string} [opts.reconnectUrl] 重连 URL 工厂
     *        （游标续订：携 after=lastSeq 重放增量，严禁整轮重发 query）
     * @returns {StreamHandle}
     */
    open: function (url, opts) {
      var controller = new AbortController();
      var aborted = false;
      var retries = 0;
      var maxRetries = (opts.reconnect === false) ? 0 : 3;
      var lastSeq = 0;
      var resolveDone;
      var done = new Promise(function (res) { resolveDone = res; });

      function headers() {
        var h = { "Accept": "text/event-stream" };
        var token = getToken();
        if (token) { h.Authorization = "Bearer " + token; }
        var sid = getSid();
        if (sid) { h["X-Session-ID"] = sid; }
        return h;
      }

      function connect() {
        // 重连优先走游标续订 URL（run_id + after）：服务端只重放增量，
        // 不重复执行编排；工厂返回空时退回原 URL（如 runId 尚未登记的极早断线）
        var target = url;
        if (retries > 0 && opts.reconnectUrl) {
          target = opts.reconnectUrl(lastSeq) || url;
        }
        fetch(target, { method: "GET", headers: headers(), signal: controller.signal })
          .then(function (resp) {
            if (resp.status === 401) {
              if (window.App && App.onAuthExpired) { App.onAuthExpired("会话已过期，请重新登录"); }
              throw new Error("unauthorized");
            }
            if (opts.onOpen) { opts.onOpen(resp.headers.get("X-Run-Id") || ""); }
            if (!resp.ok || !resp.body) {
              if (resp.status === 404) {
                // 404 = 路由不存在（旧服务进程）或 run 缓冲已过期（游标重放失败，
                // 调用方 onError 后回退本地快照）
                throw new Error("stream HTTP 404");
              }
              throw new Error("stream HTTP " + resp.status);
            }
            var reader = resp.body.getReader();
            var decoder = new TextDecoder("utf-8");
            var buf = "";

            function pump() {
              return reader.read().then(function (chunk) {
                if (chunk.done) {
                  opts.onEvent({ event: "__stream_end__", payload: {}, turn_id: "", timestamp: 0 });
                  return;
                }
                buf += decoder.decode(chunk.value, { stream: true });
                var idx;
                while ((idx = buf.indexOf("\n\n")) >= 0) {
                  var frame = buf.slice(0, idx);
                  buf = buf.slice(idx + 2);
                  var lines = frame.split("\n");
                  for (var i = 0; i < lines.length; i++) {
                    var line = lines[i];
                    if (line.indexOf("data: ") === 0) {
                      var ev = AgentProtocol.parseEvent(line.slice(6));
                      if (ev) {
                        // 游标推进：帧带 run 注册表分配的单调 seq
                        if (typeof ev.seq === "number" && ev.seq > lastSeq) { lastSeq = ev.seq; }
                        opts.onEvent(ev);
                      }
                    }
                  }
                }
                return pump();
              });
            }
            return pump();
          })
          .catch(function (err) {
            if (aborted || (err && err.name === "AbortError")) { return; }
            if (err && err.message === "unauthorized") { return; }
            if (retries < maxRetries) {
              retries++;
              var delay = Math.min(500 * Math.pow(2, retries - 1), 4000);
              setTimeout(connect, delay);
              return;
            }
            if (opts.onError) { opts.onError(err); }
            opts.onEvent({ event: "__stream_end__", payload: {}, turn_id: "", timestamp: 0 });
          });
      }

      connect();
      return {
        abort: function () { aborted = true; controller.abort(); resolveDone(); },
        done: done,
        lastSeq: function () { return lastSeq; }
      };
    }
  };

  window.AgentEventSource = agentEventSource;
})();
