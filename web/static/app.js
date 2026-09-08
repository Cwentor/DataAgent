(function () {
  "use strict";

  var COLORS = ["#4f6ef7", "#22b8cf", "#12b886", "#f59f00", "#e64980", "#845ef7", "#74b816", "#f76707"];
  var $ = function (id) { return document.getElementById(id); };
  var TOKEN_KEY = "futurebi_token";
  var SESSION_KEY = "futurebi_session";

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function fmt(v) {
    if (typeof v === "number") {
      return v.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
    }
    return String(v == null ? "" : v);
  }

  function hideError() { $("error").classList.add("hidden"); $("error").textContent = ""; }
  function showError(msg) {
    var e = $("error");
    e.textContent = msg;
    e.classList.remove("hidden");
  }

  // ---------------------------------------------------------------- Toast
  function toast(msg, type) {
    var box = $("toast-box");
    var el = document.createElement("div");
    el.className = "toast " + (type || "info");
    el.textContent = msg;
    box.appendChild(el);
    setTimeout(function () {
      el.style.opacity = "0";
      el.style.transition = "opacity .25s";
      setTimeout(function () { el.remove(); }, 260);
    }, type === "err" ? 6000 : 3000);
  }

  // ---------------------------------------------------------------- 鉴权
  function getToken() { try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch (e) { return ""; } }
  function setToken(t) { try { sessionStorage.setItem(TOKEN_KEY, t); } catch (e) {} }
  function clearToken() { try { sessionStorage.removeItem(TOKEN_KEY); } catch (e) {} }
  // 服务端签发的会话 ID（多轮上下文载体；跨轮查询必须复用同一会话）
  var SID_KEY = "futurebi_sid";
  function getSid() { try { return sessionStorage.getItem(SID_KEY) || ""; } catch (e) { return ""; } }
  function setSid(s) { try { sessionStorage.setItem(SID_KEY, s); } catch (e) {} }
  function clearSid() { try { sessionStorage.removeItem(SID_KEY); } catch (e) {} }

  function api(path, opts) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    var token = getToken();
    if (token) { opts.headers.Authorization = "Bearer " + token; }
    var sid = getSid();
    if (sid) { opts.headers["X-Session-ID"] = sid; }
    return fetch(path, opts).then(function (r) {
      return r.json().then(function (data) {
        if (r.status === 401) {
          // 会话失效 -> 回到登录态
          clearToken();
          clearSid();
          showLogin();
        }
        return data;
      });
    });
  }

  function showLogin() {
    $("login-form").classList.remove("hidden");
    $("userinfo").classList.add("hidden");
  }

  function showUser(user) {
    $("login-form").classList.add("hidden");
    $("userinfo").classList.remove("hidden");
    $("display-name").textContent = user.display_name + "（" + user.username + "）";
    var badge = $("principal-badge");
    badge.textContent = "主体：" + user.principal;
    badge.title = "数据权限主体由服务端从身份映射，客户端不可指定";
  }

  function login() {
    var username = $("username").value.trim();
    var password = $("password").value;
    if (!username || !password) { showError("请输入用户名与口令"); return; }
    var btn = $("login-btn");
    btn.disabled = true; btn.textContent = "登录中…";
    hideError();
    fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: username, password: password })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.token) {
          setToken(data.token);
          if (data.session_id) { setSid(data.session_id); }
          $("password").value = "";
          showUser(data.user);
          fillModelSwitch();  // 登录后拉取供应商模型候选（模型切换器数据源）
        } else {
          showError(data.error || "登录失败");
        }
      })
      .catch(function (err) { showError("登录请求失败：" + err); })
      .finally(function () { btn.disabled = false; btn.textContent = "登录"; });
  }

  function logout() {
    fetch("/api/auth/logout", { method: "POST" }).then(function () {
      clearToken();
      showLogin();
    }).catch(function () {
      clearToken();
      showLogin();
    });
  }

  function restoreSession() {
    api("/api/auth/me").then(function (data) {
      if (data && data.username) { showUser(data); fillModelSwitch(); }
      else { showLogin(); }
    }).catch(function () { showLogin(); });
  }

  // ---------------------------------------------------------------- 表格
  function renderTable(columns, rows) {
    var box = $("table");
    box.innerHTML = "";
    if (!columns || !columns.length) { box.textContent = "（无结果）"; return; }
    var html = "<table><thead><tr>";
    for (var i = 0; i < columns.length; i++) html += "<th>" + esc(columns[i]) + "</th>";
    html += "</tr></thead><tbody>";
    for (var r = 0; r < rows.length; r++) {
      html += "<tr>";
      for (var c = 0; c < rows[r].length; c++) {
        var v = rows[r][c];
        var cls = typeof v === "number" ? ' class="num"' : "";
        html += "<td" + cls + ">" + esc(fmt(v)) + "</td>";
      }
      html += "</tr>";
    }
    html += "</tbody></table>";
    box.innerHTML = html;
  }

  // ---------------------------------------------------------------- 图表
  function numberCard(viz, columns, rows) {
    var v = rows.length ? rows[0][0] : 0;
    var label = viz.y || (columns.length ? columns[0] : "");
    return "<div><div class='kpi'>" + esc(fmt(v)) + "</div><div class='kpi-label'>" + esc(label) + "</div></div>";
  }

  function bars(viz, columns, rows) {
    var W = 560, H = 300, padL = 40, padB = 60, padT = 20, padR = 20;
    var innerW = W - padL - padR, innerH = H - padT - padB;
    var vals = rows.map(function (r) { return Number(r[1]); });
    var max = Math.max.apply(null, vals.concat([1]));
    var n = rows.length;
    var band = innerW / Math.max(n, 1);
    var barW = Math.min(band * 0.6, 48);
    var s = "<svg viewBox='0 0 " + W + " " + H + "' xmlns='http://www.w3.org/2000/svg'>";
    for (var i = 0; i < n; i++) {
      var h = (vals[i] / max) * innerH;
      var x = padL + i * band + (band - barW) / 2;
      var y = padT + innerH - h;
      s += "<rect x='" + x.toFixed(1) + "' y='" + y.toFixed(1) + "' width='" + barW.toFixed(1) + "' height='" + h.toFixed(1) + "' rx='3' fill='" + COLORS[i % COLORS.length] + "'></rect>";
      s += "<text x='" + (x + barW / 2).toFixed(1) + "' y='" + (padT + innerH + 16) + "' text-anchor='middle' font-size='11' fill='#6b7280'>" + esc(String(rows[i][0])) + "</text>";
    }
    s += "</svg>";
    return s;
  }

  function pie(viz, columns, rows) {
    var W = 560, H = 300, cx = 150, cy = 150, r = 110;
    var vals = rows.map(function (r) { return Number(r[1]); });
    var total = vals.reduce(function (a, b) { return a + b; }, 0) || 1;
    var angle = -Math.PI / 2;
    var s = "<svg viewBox='0 0 " + W + " " + H + "' xmlns='http://www.w3.org/2000/svg'>";
    for (var i = 0; i < vals.length; i++) {
      var frac = vals[i] / total;
      var end = angle + frac * 2 * Math.PI;
      var x1 = cx + r * Math.cos(angle), y1 = cy + r * Math.sin(angle);
      var x2 = cx + r * Math.cos(end), y2 = cy + r * Math.sin(end);
      var large = frac > 0.5 ? 1 : 0;
      s += "<path d='M " + cx + " " + cy + " L " + x1.toFixed(2) + " " + y1.toFixed(2) + " A " + r + " " + r + " 0 " + large + " 1 " + x2.toFixed(2) + " " + y2.toFixed(2) + " Z' fill='" + COLORS[i % COLORS.length] + "'></path>";
      angle = end;
    }
    var lx = 300, ly = 40;
    for (var j = 0; j < rows.length && j < 10; j++) {
      var pct = (vals[j] / total * 100).toFixed(1);
      s += "<rect x='" + lx + "' y='" + (ly + j * 24) + "' width='12' height='12' rx='2' fill='" + COLORS[j % COLORS.length] + "'></rect>";
      s += "<text x='" + (lx + 18) + "' y='" + (ly + j * 24 + 11) + "' font-size='12' fill='#1a1d2e'>" + esc(String(rows[j][0]) + " (" + pct + "%)") + "</text>";
    }
    s += "</svg>";
    return s;
  }

  function line(viz, columns, rows) {
    var W = 560, H = 300, padL = 50, padB = 60, padT = 20, padR = 20;
    var innerW = W - padL - padR, innerH = H - padT - padB;
    var vals = rows.map(function (r) { return Number(r[1]); });
    var max = Math.max.apply(null, vals.concat([1]));
    var n = rows.length;
    var pts = "";
    for (var i = 0; i < n; i++) {
      var x = padL + (n === 1 ? innerW / 2 : (i / (n - 1)) * innerW);
      var y = padT + innerH - (vals[i] / max) * innerH;
      pts += (i ? " " : "") + x.toFixed(1) + "," + y.toFixed(1);
    }
    var s = "<svg viewBox='0 0 " + W + " " + H + "' xmlns='http://www.w3.org/2000/svg'>";
    s += "<polyline points='" + pts + "' fill='none' stroke='#4f6ef7' stroke-width='2.5'></polyline>";
    var step = Math.max(1, Math.floor((n - 1) / 8));
    for (var k = 0; k < n; k++) {
      var lx = padL + (n === 1 ? innerW / 2 : (k / (n - 1)) * innerW);
      var ly = padT + innerH - (vals[k] / max) * innerH;
      s += "<circle cx='" + lx.toFixed(1) + "' cy='" + ly.toFixed(1) + "' r='3' fill='#4f6ef7'></circle>";
      if (k % step === 0 || k === n - 1) {
        var label = String(rows[k][0]).slice(0, 10);
        s += "<text x='" + lx.toFixed(1) + "' y='" + (padT + innerH + 16) + "' text-anchor='middle' font-size='11' fill='#6b7280'>" + esc(label) + "</text>";
      }
    }
    s += "</svg>";
    return s;
  }

  function renderChart(viz, columns, rows) {
    var box = $("chart");
    box.innerHTML = "";
    if (!viz || !rows || !rows.length) { box.textContent = "（无数据）"; return; }
    var chart = viz.chart;
    if (chart === "number") { box.innerHTML = numberCard(viz, columns, rows); }
    else if (chart === "bar") { box.innerHTML = bars(viz, columns, rows); }
    else if (chart === "pie") { box.innerHTML = pie(viz, columns, rows); }
    else if (chart === "line") { box.innerHTML = line(viz, columns, rows); }
    else if (chart === "pivot") { renderPivot(viz, columns, rows); }
    else { box.textContent = "该结果以表格形式展示"; }
  }

  // 透视表（pivot）真实渲染：完整分组表格 + 维度/指标说明提示（报告整改指令3-1）
  function renderPivot(viz, columns, rows) {
    var box = $("chart");
    box.innerHTML = "";
    var html = "<div class='pivot-note'>多维结果以分组表格展示（维度："
      + esc((viz.x || "—")) + "；指标：" + esc((viz.y || "—")) + "）</div>";
    html += "<div class='table-wrap'><table><thead><tr>";
    for (var i = 0; i < columns.length; i++) html += "<th>" + esc(columns[i]) + "</th>";
    html += "</tr></thead><tbody>";
    for (var r = 0; r < rows.length; r++) {
      html += "<tr>";
      for (var c = 0; c < rows[r].length; c++) {
        var v = rows[r][c];
        var cls = typeof v === "number" ? ' class="num"' : "";
        html += "<td" + cls + ">" + esc(fmt(v)) + "</td>";
      }
      html += "</tr>";
    }
    html += "</tbody></table></div>";
    box.innerHTML = html;
  }

  // ---------------------------------------------------------------- 意图路由结果
  function clearAnswer() {
    $("answer").classList.add("hidden");
    $("answer-title").textContent = "";
    $("answer-body").innerHTML = "";
  }

  function showAnswer(title, html) {
    $("answer-title").textContent = title;
    $("answer-body").innerHTML = html;
    $("answer").classList.remove("hidden");
  }

  function clearPipeline() {
    $("steps").innerHTML = "";
    $("dsl").textContent = "";
    $("sql").textContent = "";
    $("explain").textContent = "";
    $("chart").innerHTML = "";
    $("table").innerHTML = "";
  }

  function renderClarifications(clarifications) {
    var items = (clarifications || []).map(function (c) {
      var tag = c.kind === "missing_time_window" ? "缺少时间窗口" : "未定义指标";
      return "<div class='clarify-item'><span class='clarify-tag'>" + esc(tag) + "</span>"
        + "<span>" + esc(c.question) + "</span></div>";
    }).join("");
    showAnswer("需要补充信息", items || "请补充更多信息后再查询。");
  }

  function renderDocuments(documents) {
    var items = (documents || []).map(function (d) {
      return "<div class='doc-item'><div class='doc-title'>" + esc(d.title) + "</div>"
        + "<div class='doc-def'>" + esc(d.definition) + "</div>"
        + "<code class='doc-formula'>" + esc(d.formula) + "</code></div>";
    }).join("");
    showAnswer("口径文档（RAG 检索结果）", items || "未检索到相关口径文档。");
  }

  // ---------------------------------------------------------------- 调度轨迹 + 导出下载
  function renderSteps(steps) {
    var box = $("steps");
    box.innerHTML = "";
    if (!steps || !steps.length) {
      box.innerHTML = "<div class='step-empty'>本次未调用工具（直接回答 / 澄清 / 闲聊）</div>";
      return;
    }
    var html = "<ol class='step-list'>";
    for (var i = 0; i < steps.length; i++) {
      var s = steps[i];
      var badge = s.success
        ? "<span class='step-badge ok'>成功</span>"
        : "<span class='step-badge fail'>失败</span>";
      var args = s.args ? esc(JSON.stringify(s.args, null, 1)) : "";
      var err = s.error_msg ? "<div class='step-err'>" + esc(s.error_msg) + "</div>" : "";
      html += "<li class='step-item'>"
        + "<div class='step-head'><span class='step-tool'>" + esc(s.tool) + "</span>"
        + badge
        + "<span class='step-dur'>" + fmt(s.duration_ms) + " ms</span></div>"
        + "<pre class='step-args'>" + args + "</pre>"
        + err
        + "</li>";
    }
    html += "</ol>";
    box.innerHTML = html;
  }

  function renderDownloads(urls) {
    if (!urls || !urls.length) { return ""; }
    var links = urls.map(function (u) {
      return "<a class='dl-link' href='" + esc(u) + "' download>⬇ 下载导出文件</a>";
    }).join(" ");
    return "<div class='downloads'>" + links + "</div>";
  }

  function renderInsight(data) {
    // 综合洞察：多轮上下文说明 + 工具答案 + 导出下载链接
    var html = "";
    if (data.context_summary) {
      html += "<div class='ctx-summary'>🔁 " + esc(data.context_summary) + "</div>";
    }
    html += "<div class='insight'>" + esc(data.answer || data.explanation || "") + "</div>";
    html += renderDownloads(data.download_urls);
    showAnswer("分析结果", html || "（无）");
  }

  // ---------------------------------------------------------------- 模型供应商设置
  var PROTOCOL_LABELS = {
    openai_chat: "Chat Completions",
    openai_responses: "Responses",
    anthropic: "Anthropic",
    gemini: "Gemini"
  };
  var CAPABILITY_LABELS = { vision: "视觉", function_calling: "函数调用", json_schema: "JSON Schema" };
  var providers = [];          // 全量供应商（脱敏视图）
  var currentProviderId = "";  // 设置面板当前编辑的供应商
  var CURRENT_SELECTION_KEY = "futurebi_model_selection";

  function getCurrentSelection() {
    try { return sessionStorage.getItem(CURRENT_SELECTION_KEY) || ""; } catch (e) { return ""; }
  }
  function setCurrentSelection(v) {
    try { sessionStorage.setItem(CURRENT_SELECTION_KEY, v); } catch (e) {}
  }

  function selectedProviderModel() {
    // 把 "provider|model" 选择值拆为请求体字段；空值 = 默认分派
    var v = $("model-switch").value;
    if (!v) return {};
    var idx = v.indexOf("|");
    return { provider_id: v.slice(0, idx), model_id: v.slice(idx + 1) };
  }

  function fillModelSwitch() {
    var sel = $("model-switch");
    var saved = getCurrentSelection();
    fetchModelChoices(function (choices) {
      var html = "<option value=''>默认模型（自动选择）</option>";
      (choices || []).forEach(function (p) {
        html += "<optgroup label='" + esc(p.provider_name) + "'>";
        (p.models || []).forEach(function (m) {
          var value = p.provider_id + "|" + m.id;
          html += "<option value='" + esc(value) + "'>"
            + esc(m.name || m.id) + " · " + (PROTOCOL_LABELS[p.protocol] || p.protocol) + "</option>";
        });
        html += "</optgroup>";
      });
      sel.innerHTML = html;
      if (saved && sel.querySelector("option[value='" + saved.replace(/"/g, '\\"') + "']")) {
        sel.value = saved;
      }
    });
  }

  function fetchModelChoices(cb) {
    api("/api/settings/providers").then(function (data) {
      providers = data.providers || [];
      renderProviderList();
      cb(data.choices || []);
    }).catch(function () { cb([]); });
  }

  // ---------------------------------------------------------------- 设置弹窗渲染
  function renderProviderList() {
    var box = $("provider-list");
    var html = "";
    providers.forEach(function (p) {
      var badge = p.is_preset
        ? "<span class='p-badge preset'>预置</span>"
        : "<span class='p-badge custom'>自定义</span>";
      if (!p.enabled) { badge += "<span class='p-badge off'>已禁用</span>"; }
      html += "<div class='provider-item" + (p.id === currentProviderId ? " active" : "") + "'"
        + " data-id='" + esc(p.id) + "'>"
        + "<span class='p-name'>" + esc(p.name) + "</span>" + badge + "</div>";
    });
    box.innerHTML = html || "<div class='provider-empty'>暂无供应商</div>";
    box.querySelectorAll(".provider-item").forEach(function (el) {
      el.addEventListener("click", function () { openProvider(el.getAttribute("data-id")); });
    });
  }

  function openProvider(id) {
    currentProviderId = id;
    renderProviderList();
    var p = providers.find(function (x) { return x.id === id; });
    $("provider-form").classList.remove("hidden");
    $("provider-empty").classList.add("hidden");
    $("pf-delete").classList.toggle("hidden", !!(p && p.is_preset));
    $("pf-test-result").classList.add("hidden");
    $("pf-name").value = p ? p.name : "";
    $("pf-enabled").checked = p ? !!p.enabled : true;
    $("pf-protocol").value = p ? p.protocol : "openai_chat";
    $("pf-base-url").value = p ? p.base_url : "";
    $("pf-api-key").value = p ? p.api_key : "";   // 脱敏串（保持即不修改）
    $("pf-api-key").type = "password";
    renderModelChips(p ? p.models : []);
  }

  function openNewProvider() {
    currentProviderId = "";
    renderProviderList();
    $("provider-form").classList.remove("hidden");
    $("provider-empty").classList.add("hidden");
    $("pf-delete").classList.add("hidden");
    $("pf-test-result").classList.add("hidden");
    $("pf-name").value = "";
    $("pf-enabled").checked = true;
    $("pf-protocol").value = "openai_chat";
    $("pf-base-url").value = "";
    $("pf-api-key").value = "";
    $("pf-api-key").type = "password";
    renderModelChips([]);
    $("pf-name").focus();
  }

  function currentFormModels() {
    return window.__pfModels || [];
  }
  function setCurrentFormModels(models) {
    window.__pfModels = models || [];
  }

  function renderModelChips(models) {
    setCurrentFormModels(models);
    var box = $("pf-models");
    if (!models || !models.length) {
      box.innerHTML = "<div class='provider-empty' style='padding:8px 0'>尚未配置模型</div>";
      return;
    }
    var html = "";
    models.forEach(function (m, i) {
      var tags = "";
      (m.capabilities || []).forEach(function (c) {
        if (CAPABILITY_LABELS[c]) { tags += "<span class='chip-tag'>" + CAPABILITY_LABELS[c] + "</span>"; }
      });
      if (m.context_window) {
        tags += "<span class='chip-tag ctx'>上下文 " + fmt(m.context_window) + "</span>";
      }
      html += "<span class='model-chip'>"
        + "<span class='m-id'>" + esc(m.id) + "</span>" + tags
        + "<button type='button' class='chip-act' data-act='test' data-i='" + i + "' title='测试该模型连通性'>⚡</button>"
        + "<button type='button' class='chip-act chip-del' data-act='del' data-i='" + i + "' title='移除模型'>✕</button>"
        + "</span>";
    });
    box.innerHTML = html;
    box.querySelectorAll(".chip-act").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var i = Number(btn.getAttribute("data-i"));
        var act = btn.getAttribute("data-act");
        var models2 = currentFormModels();
        if (act === "del") {
          models2.splice(i, 1);
          renderModelChips(models2);
        } else if (act === "test") {
          testConnection({ model_id: models2[i].id });
        }
      });
    });
  }

  function collectForm() {
    return {
      name: $("pf-name").value.trim(),
      enabled: $("pf-enabled").checked,
      protocol: $("pf-protocol").value,
      base_url: $("pf-base-url").value.trim(),
      api_key: $("pf-api-key").value,          // 原样提交：脱敏串由服务端识别为"不修改"
      models: currentFormModels()
    };
  }

  function saveProvider() {
    var form = collectForm();
    if (!form.name) { toast("请填写供应商名称", "err"); return; }
    if (!form.base_url) { toast("请填写 Base URL", "err"); return; }
    var body = JSON.stringify(form);
    if (currentProviderId) {
      api("/api/settings/providers/" + encodeURIComponent(currentProviderId), {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: body
      }).then(handleSaved);
    } else {
      api("/api/settings/providers", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: body
      }).then(handleSaved);
    }
  }

  function handleSaved(data) {
    if (data.error) { toast(data.error, "err"); return; }
    toast("供应商配置已保存", "ok");
    currentProviderId = data.provider ? data.provider.id : currentProviderId;
    fetchModelChoices(function () { openProvider(currentProviderId); });
    refreshModelSwitch();
  }

  function deleteProvider() {
    if (!currentProviderId) { return; }
    if (!window.confirm("确认删除该供应商？删除后不可恢复。")) { return; }
    api("/api/settings/providers/" + encodeURIComponent(currentProviderId), { method: "DELETE" })
      .then(function (data) {
        if (data.error) { toast(data.error, "err"); return; }
        toast("供应商已删除", "ok");
        currentProviderId = "";
        $("provider-form").classList.add("hidden");
        $("provider-empty").classList.remove("hidden");
        fetchModelChoices(function () {});
        refreshModelSwitch();
      });
  }

  function testConnection(extra) {
    var payload = extra || {};
    var form = collectForm();
    if (currentProviderId && $("pf-api-key").value === "") {
      // 未填写 Key 时按已保存配置测（服务端保留原 Key）
      payload.provider_id = currentProviderId;
      if (!payload.model_id) { payload.model_id = (form.models[0] || {}).id || ""; }
    } else {
      // 按当前表单临时配置测（未保存也能先验证连通性）
      payload.base_url = form.base_url;
      payload.api_key = form.api_key;
      payload.protocol = form.protocol;
      payload.custom_headers = {};
      if (!payload.model_id) {
        payload.model_id = (form.models[0] || {}).id || $("pf-model-input").value.trim();
      }
    }
    if (!payload.model_id) { toast("请先添加或填写要测试的模型 ID", "err"); return; }
    var btn = $("pf-test");
    btn.disabled = true; btn.textContent = "测试中…";
    api("/api/settings/providers/test", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (data) {
      var box = $("pf-test-result");
      box.classList.remove("hidden");
      if (data.success) {
        box.className = "test-result ok";
        box.textContent = "✓ 连接成功 · 延时 " + fmt(data.latency_ms) + " ms";
        toast("连接成功（" + fmt(data.latency_ms) + " ms）", "ok");
      } else {
        box.className = "test-result fail";
        box.textContent = "✗ " + (data.error || "连接失败");
        toast(data.error || "连接失败", "err");
      }
    }).catch(function (err) {
      toast("测试请求失败：" + err, "err");
    }).finally(function () {
      btn.disabled = false; btn.textContent = "测试连通性";
    });
  }

  function refreshModelSwitch() {
    fillModelSwitch();
  }

  function openSettings() {
    $("settings-modal").classList.remove("hidden");
    fetchModelChoices(function () {});
  }
  function closeSettings() {
    $("settings-modal").classList.add("hidden");
    refreshModelSwitch();
  }

  function initSettingsUi() {
    $("settings-btn").addEventListener("click", openSettings);
    $("settings-close").addEventListener("click", closeSettings);
    $("settings-modal").addEventListener("click", function (e) {
      if (e.target === $("settings-modal")) { closeSettings(); }
    });
    $("provider-add").addEventListener("click", openNewProvider);
    $("pf-model-add").addEventListener("click", function () {
      var input = $("pf-model-input");
      var id = input.value.trim();
      if (!id) { toast("请输入模型 ID", "err"); return; }
      var models = currentFormModels();
      if (models.some(function (m) { return m.id === id; })) { toast("模型已存在", "err"); return; }
      models.push({ id: id, name: id, capabilities: [], context_window: null });
      input.value = "";
      renderModelChips(models);
    });
    $("pf-model-input").addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); $("pf-model-add").click(); }
    });
    $("pf-eye").addEventListener("click", function () {
      var input = $("pf-api-key");
      input.type = input.type === "password" ? "text" : "password";
    });
    $("pf-save").addEventListener("click", saveProvider);
    $("pf-delete").addEventListener("click", deleteProvider);
    $("pf-test").addEventListener("click", function () { testConnection(null); });
    $("model-switch").addEventListener("change", function () {
      setCurrentSelection($("model-switch").value);
      var sel = selectedProviderModel();
      if (sel.provider_id) {
        toast("本次查询将使用：" + sel.provider_id + " / " + sel.model_id, "info");
      }
    });
  }

  // ---------------------------------------------------------------- 主流程
  function render(data) {
    clearPipeline();
    clearAnswer();
    renderSteps(data.steps);
    if (data.action === "chitchat") {
      showError(data.message || data.error || "抱歉，只能回答数据分析相关问题。");
      return;
    }
    if (data.action === "clarify") {
      hideError();
      renderClarifications(data.clarifications);
      return;
    }
    if (data.action === "rag") {
      hideError();
      renderDocuments(data.documents);
      return;
    }
    if (data.error) {
      showError(data.error);
      // 供应商错误（鉴权失败 / 配额超限 / 超时等）额外 Toast 可理解提示（DoD 4）
      if (/模型服务|鉴权|配额|供应商/.test(data.error)) { toast(data.error, "err"); }
      return;
    }
    hideError();
    renderInsight(data);
    $("dsl").textContent = JSON.stringify(data.dsl, null, 2);
    $("sql").textContent = data.sql;
    $("explain").textContent = data.explanation || "";
    renderChart(data.viz, data.columns, data.rows);
    renderTable(data.columns, data.rows);
  }

  function run() {
    var q = $("query").value.trim();
    if (!q) { showError("请输入问题"); return; }
    if (!getToken()) { showError("请先登录后再查询"); return; }
    hideError();
    var btn = $("run");
    btn.disabled = true;
    btn.textContent = "查询中…";
    // 客户端不再提交 principal：主体由服务端从身份映射（P0）。
    // 模型切换器取值随请求透传 provider_id / model_id（请求级模型切换）
    var payload = { query: q };
    var sel = selectedProviderModel();
    if (sel.provider_id) { payload.provider_id = sel.provider_id; payload.model_id = sel.model_id; }
    api("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    })
      .then(function (data) { render(data); })
      .catch(function (err) { showError("请求失败：" + err); })
      .finally(function () {
        btn.disabled = false;
        btn.textContent = "查询";
      });
  }

  $("login-btn").addEventListener("click", function (e) { e.preventDefault(); login(); });
  $("login-form").addEventListener("submit", function (e) { e.preventDefault(); login(); });
  $("logout-btn").addEventListener("click", logout);
  $("run").addEventListener("click", run);
  $("query").addEventListener("keydown", function (e) { if (e.key === "Enter") run(); });
  initSettingsUi();
  restoreSession();
  run();
})();
