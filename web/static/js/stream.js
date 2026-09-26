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

  /** 默认停滞阈值：连续 120s 未收到任何字节（含服务端 15s 心跳）判定连接已死。
   *  服务端心跳由订阅线程独立驱动，慢任务（LLM 最坏 120s）期间照发，
   *  因此该阈值判定的是"连接是否活着"而非"任务是否慢"。 */
  var DEFAULT_STALL_MS = 120000;

  /** 停滞自救额度：判定零字节后不立即上屏中断，先按游标续订静默重连
   *  （服务端 run 仍在执行、事件持续缓冲，after=<lastSeq> 增量重放不重跑编排）。
   *  单次停滞连续重连 3 次；整条流累计 6 次（防链路持续抖动时无限自救循环）。 */
  var STALL_RESUME_MAX = 3;
  var STALL_RESUME_TOTAL_MAX = 6;

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
     * @param {() => void} [opts.onStall] 连接停滞且回调：停滞自救（游标续订）
     *        额度耗尽后触发，调用方据此把中断上屏；流自身随即终止且不再重试
     * @param {number} [opts.stallTimeoutMs] 停滞阈值（默认 120000；0 = 关闭看门狗）
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
      var resumeLeft = STALL_RESUME_MAX; // 本轮停滞的自救额度（收到字节即回满）
      var resumesTotal = 0; // 整条流累计自救次数（硬上限防持续抖动时无限循环）
      var sawTerminal = false; // 已见 done/error/hitl_request：收尾属正常，不自救
      var resumePending = false; // 自救重连走游标续订（与网络重试额度解耦）
      var reconnecting = false; // 已有延迟重连在途（防自救路径双重 connect 竞态）
      // 停滞看门狗：零字节超时 => 连接半死（休眠 / 代理丢包 / TCP 半开），
      // fetch/read 永不 reject 的静默挂起在此被主动发现
      var stallMs = opts.stallTimeoutMs == null ? DEFAULT_STALL_MS : opts.stallTimeoutMs;
      var lastChunkAt = Date.now();
      var stallTimer = null;

      function stopWatchdog() {
        if (stallTimer) { clearInterval(stallTimer); stallTimer = null; }
      }

      function startWatchdog() {
        if (!stallMs || stallTimer) { return; }
        // 检查间隔取阈值 1/8（最短 1s）：既及时又不至于高频空转
        var tick = Math.max(1000, Math.floor(stallMs / 8));
        stallTimer = setInterval(function () {
          if (aborted || Date.now() - lastChunkAt < stallMs) { return; }
          // 零字节停滞（连接半死：休眠 / TCP 半开 / 同主机连接排队饿死）。
          // 先自救：中止旧读取 + 换新 AbortController，按游标续订重连
          // （服务端 run 不受影响，after=lastSeq 增量重放，严禁重跑编排）；
          // 额度耗尽或调用方禁用重连（如快照重放）才判死并上屏中断。
          var canResume = opts.reconnect !== false && opts.reconnectUrl
            && resumeLeft > 0 && resumesTotal < STALL_RESUME_TOTAL_MAX;
          if (canResume) {
            resumeLeft--;
            resumesTotal++;
            resumePending = true; // connect() 走 reconnectUrl 游标续订分支
            stopWatchdog();
            try { controller.abort(); } catch (e) { /* 旧 fetch 立即失效 */ }
            controller = new AbortController();
            lastChunkAt = Date.now(); // 自救后重新计时
            startWatchdog();
            if (!reconnecting) {
              reconnecting = true; // 单飞：chunk.done 自救路径不得再排一次 connect
              setTimeout(connect, 1000);
            }
            return;
          }
          stopWatchdog();
          aborted = true; // 复用早退分支：阻止后续重连与事件处理
          try { controller.abort(); } catch (e) { /* 同上 */ }
          if (opts.onStall) {
            try { opts.onStall(); } catch (e) { /* 回调异常不阻断收尾 */ }
          }
          opts.onEvent({ event: "__stream_end__", payload: { reason: "stall" }, turn_id: "", timestamp: 0 });
          resolveDone();
        }, tick);
      }

      function headers() {
        var h = { "Accept": "text/event-stream" };
        var token = getToken();
        if (token) { h.Authorization = "Bearer " + token; }
        var sid = getSid();
        if (sid) { h["X-Session-ID"] = sid; }
        return h;
      }

      function connect() {
        if (aborted) { return; } // abort 落在重连延迟窗口内：不再拉起新连接
        reconnecting = false; // 本次连接已兑现，解除单飞互斥
        // 重连优先走游标续订 URL（run_id + after）：服务端只重放增量，
        // 不重复执行编排；工厂返回空时退回原 URL（如 runId 尚未登记的极早断线）
        var target = url;
        if ((retries > 0 || resumePending) && opts.reconnectUrl) {
          target = opts.reconnectUrl(lastSeq) || url;
        }
        resumePending = false; // 连接意图已表达；成功与否由后续字节/异常决定
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
                // 调用方 onError 后回退本地快照）。终态：重试必败，立即收敛。
                var notFound = new Error("stream HTTP 404");
                notFound.terminal = true;
                throw notFound;
              }
              throw new Error("stream HTTP " + resp.status);
            }
            var reader = resp.body.getReader();
            var decoder = new TextDecoder("utf-8");
            var buf = "";

            function pump() {
              return reader.read().then(function (chunk) {
                if (chunk.done) {
                  // 未到终态即收尾 = 服务端订阅线程写失败 / 进程重启 / 网络提前断流。
                  // run 可能仍在服务端执行并缓冲——先按游标静默续订自救（增量重放，
                  // 不重跑编排），额度耗尽才走正常收尾交调用方兜底
                  var canHeal = !sawTerminal && opts.reconnect !== false && !!opts.reconnectUrl
                    && resumeLeft > 0 && resumesTotal < STALL_RESUME_TOTAL_MAX;
                  if (canHeal) {
                    resumeLeft--;
                    resumesTotal++;
                    resumePending = true; // connect() 走 reconnectUrl 游标续订分支
                    stopWatchdog();
                    lastChunkAt = Date.now(); // 重新计时，避免旧停滞立即误触发
                    startWatchdog();
                    if (!reconnecting) {
                      reconnecting = true; // 单飞：与停滞自救路径互斥
                      setTimeout(connect, 500);
                    }
                    return;
                  }
                  stopWatchdog();
                  opts.onEvent({ event: "__stream_end__", payload: { reason: "closed" }, turn_id: "", timestamp: 0 });
                  resolveDone(); // 正常收尾：handle.done 在全部终止路径都 resolve
                  return;
                }
                // 活动信号取「字节到达」而非业务事件：服务端 : ping 心跳同样续命，
                // 保证慢任务期间不会被误判为连接中断
                lastChunkAt = Date.now();
                resumeLeft = STALL_RESUME_MAX; // 链路恢复即回满自救额度
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
                        // done/error = 正常终态；hitl_request = 服务端 paused 收尾
                        // （三种情形随后的连接关闭都是预期行为，不触发自救重连）
                        if (ev.event === "done" || ev.event === "error"
                          || ev.event === "hitl_request") { sawTerminal = true; }
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
            if (err && err.message === "unauthorized") { stopWatchdog(); resolveDone(); return; }
            if (err && err.terminal) {
              // 404 等"重试必败"终态：不再消耗重试额度，直接交调用方收敛
              stopWatchdog();
              if (opts.onError) { opts.onError(err); }
              opts.onEvent({ event: "__stream_end__", payload: { reason: "gone" }, turn_id: "", timestamp: 0 });
              resolveDone();
              return;
            }
            if (retries < maxRetries) {
              retries++;
              var delay = Math.min(500 * Math.pow(2, retries - 1), 4000);
              // 重连期间继续监视：断线到重连成功之间也属"零字节"，同样应被察觉
              setTimeout(connect, delay);
              return;
            }
            stopWatchdog();
            if (opts.onError) { opts.onError(err); }
            opts.onEvent({ event: "__stream_end__", payload: { reason: "network" }, turn_id: "", timestamp: 0 });
            resolveDone();
          });
      }

      connect();
      startWatchdog();
      return {
        abort: function () { aborted = true; stopWatchdog(); controller.abort(); resolveDone(); },
        done: done,
        lastSeq: function () { return lastSeq; }
      };
    }
  };

  window.AgentEventSource = agentEventSource;
})();
