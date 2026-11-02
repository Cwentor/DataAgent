/**
 * 语义目录管理面板（issue #27）：分节卡片 + JSON 编辑 + 试校验 + 受控保存。
 *
 * 自包含模块（与 sidebar-ui.js 同模式）：自带 $/esc/toast/认证头，经
 * window.CatalogUI.open() 由 app.js 用户菜单拉起。
 *
 * 治理语义（与服务端 catalog_api 对齐）：
 * - config/semantic.json 是唯一事实源，本面板只是受控写入界面；
 * - 可写节编辑提交走 PUT（节整体替换），保存必带当前版本哈希（乐观锁），
 *   409 冲突时提示并自动重拉最新内容；
 * - 试校验前置：服务端 catalog_loader 全量校验通过才允许落盘；
 * - 结构性契约节只读展示，写端点由服务端 400 拒绝（前端不提供入口）。
 *
 * 交互绑定纪律：卡片为重渲染元素，按钮一律走容器级事件委托
 * （data-action 属性路由），严禁每次渲染命令式 addEventListener。
 */
(function () {
  "use strict";

  // ---------------------------------------------------------------- 工具
  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function toast(msg, type) {
    var box = $("toast-box");
    if (!box) { window.alert(msg); return; }
    var el = document.createElement("div");
    el.className = "toast " + (type || "info");
    el.textContent = msg;
    box.appendChild(el);
    setTimeout(function () {
      el.style.opacity = "0";
      el.style.transition = "opacity .25s";
      setTimeout(function () { el.remove(); }, 300);
    }, 3200);
  }

  function authHeaders(extra) {
    var headers = extra || {};
    var token = sessionStorage.getItem("dataagent_token") || localStorage.getItem("dataagent_token") || "";
    if (token) { headers.Authorization = "Bearer " + token; }
    var sid = sessionStorage.getItem("dataagent_sid") || localStorage.getItem("dataagent_session") || "";
    if (sid) { headers["X-Session-ID"] = sid; }
    return headers;
  }

  /** JSON API 封装：返回 {status, body}；401 上抛全局钩子。 */
  function apiJson(path, method, payload) {
    var opts = { method: method || "GET", headers: authHeaders({}) };
    if (payload !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(payload);
    }
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (body) {
        if (r.status === 401 && window.App && window.App.onAuthExpired) {
          window.App.onAuthExpired("会话已过期，请重新登录");
        }
        return { status: r.status, body: body };
      });
    });
  }

  // ---------------------------------------------------------------- 状态与常量
  var state = { version: "", data: {}, editable: [], readonly: [] };

  var SECTION_LABELS = {
    aliases: "表别名",
    metric_aliases: "聚合产物中文映射",
    value_labels: "枚举值中文标签",
    table_labels: "表中文标签",
    region_province_mapping: "大区与省份映射",
    dimension_members_seed: "维度成员离线种子"
  };

  // ---------------------------------------------------------------- 渲染
  function sectionLabel(name) {
    return SECTION_LABELS[name] || name;
  }

  function renderMeta() {
    $("catalog-meta").innerHTML =
      '<span class="cat-version">版本 <code>' + esc(state.version) + "</code></span>"
      + '<span class="cat-hint">可编辑节 ' + state.editable.length
      + " · 结构性只读节 " + state.readonly.length + "（开放编辑需单独立项评审）</span>";
  }

  function renderSections() {
    var box = $("catalog-sections");
    var names = state.editable.concat(state.readonly);
    var html = "";
    names.forEach(function (name) {
      var editable = state.editable.indexOf(name) !== -1;
      var value = state.data[name];
      html += '<details class="cat-card' + (editable ? " editable" : "") + '" data-section="' + esc(name) + '">'
        + '<summary class="cat-card-head">'
        + '<span class="cat-name">' + esc(name) + "</span>"
        + '<span class="cat-label">' + esc(sectionLabel(name)) + "</span>"
        + (editable ? '<span class="cat-badge ok">可编辑</span>' : '<span class="cat-badge ro">只读</span>')
        + "</summary>"
        + '<div class="cat-card-body">';
      if (editable) {
        html += '<textarea class="cat-json" rows="10" spellcheck="false"'
          + ' data-json="' + esc(name) + '">' + esc(JSON.stringify(value, null, 2)) + "</textarea>"
          + '<div class="cat-actions">'
          + '<button type="button" class="mini-btn" data-action="validate" data-section="' + esc(name) + '">试校验</button>'
          + '<button type="button" class="mini-btn primary" data-action="save" data-section="' + esc(name) + '">保存</button>'
          + "</div>"
          + '<div class="cat-result" data-result="' + esc(name) + '"></div>';
      } else {
        html += '<pre class="cat-json ro">' + esc(JSON.stringify(value, null, 2)) + "</pre>";
      }
      html += "</div></details>";
    });
    box.innerHTML = html || '<div class="hp-empty">语义目录为空</div>';
  }

  function renderResult(name, html) {
    var el = document.querySelector('[data-result="' + name + '"]');
    if (el) { el.innerHTML = html; }
  }

  /** 校验/保存结果统一渲染：diff 与错误均为结构化清单。 */
  function renderValidateResult(name, body, fallbackError) {
    if (!body || body.error && !body.errors) {
      renderResult(name, '<div class="cat-err">' + esc((body && body.error) || fallbackError) + "</div>");
      return;
    }
    var html = "";
    if (body.errors && body.errors.length) {
      html += '<div class="cat-err"><b>校验失败</b><ul>';
      body.errors.forEach(function (e) { html += "<li>" + esc(e) + "</li>"; });
      html += "</ul></div>";
    } else {
      html += '<div class="cat-ok">校验通过</div>';
    }
    (body.diff || []).forEach(function (d) {
      html += '<div class="cat-diff">[' + esc(d.section) + "] "
        + "新增 " + d.added.length + " · 修改 " + d.updated.length + " · 删除 " + d.removed.length
        + (d.updated.length ? "（" + esc(d.updated.join("、")) + "）" : "")
        + "</div>";
    });
    (body.impact || []).forEach(function (hint) {
      html += '<div class="cat-impact">影响面：' + esc(hint) + "</div>";
    });
    if (body.current_version) {
      html += '<div class="cat-err">当前版本已变为 <code>' + esc(body.current_version) + "</code></div>";
    }
    renderResult(name, html);
  }

  // ---------------------------------------------------------------- 动作
  function parseSection(name) {
    var el = document.querySelector('[data-json="' + name + '"]');
    try {
      var parsed = JSON.parse(el.value);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("该节必须是 JSON 对象");
      }
      return { ok: true, value: parsed };
    } catch (err) {
      return { ok: false, error: "JSON 解析失败：" + err.message };
    }
  }

  function afterWrite(name, body) {
    state.version = body.version;
    state.data[name] = parseSection(name).value;
    renderMeta();
    renderValidateResult(name, body);
    toast("语义目录已保存并热重载生效", "ok");
  }

  function handleAction(action, name) {
    var parsed = parseSection(name);
    if (!parsed.ok) {
      renderResult(name, '<div class="cat-err">' + esc(parsed.error) + "</div>");
      return;
    }
    var sections = {};
    sections[name] = parsed.value;
    if (action === "validate") {
      apiJson("/api/catalog/validate", "POST", { version: state.version, sections: sections })
        .then(function (r) { renderValidateResult(name, r.body, "试校验请求失败"); });
      return;
    }
    // 保存：PUT 节整体替换（必带版本哈希；409 由服务端乐观锁裁决）
    apiJson("/api/catalog", "PUT", { version: state.version, sections: sections })
      .then(function (r) {
        if (r.status === 200) { afterWrite(name, r.body); return; }
        if (r.status === 409) {
          toast("目录已被他人修改，已为你刷新最新内容，请重新编辑提交", "err");
          load();
          return;
        }
        renderValidateResult(name, r.body, "保存请求失败");
      });
  }

  // ---------------------------------------------------------------- 装载与入口
  function load() {
    $("catalog-sections").innerHTML = '<div class="hp-empty">加载中…</div>';
    apiJson("/api/catalog").then(function (r) {
      if (!r.body || r.body.error) {
        $("catalog-sections").innerHTML =
          '<div class="hp-empty">' + esc((r.body && r.body.error) || "加载失败") + "</div>";
        return;
      }
      state.version = r.body.version;
      state.data = r.body.data || {};
      state.editable = r.body.editable_sections || [];
      state.readonly = r.body.readonly_sections || [];
      renderMeta();
      renderSections();
    });
  }

  function open() {
    $("catalog-modal").classList.remove("hidden");
    load();
  }

  function init() {
    $("catalog-close").addEventListener("click", function () {
      $("catalog-modal").classList.add("hidden");
    });
    $("catalog-modal").addEventListener("click", function (e) {
      if (e.target === $("catalog-modal")) { $("catalog-modal").classList.add("hidden"); }
    });
    // 卡片按钮事件委托：重渲染不丢监听（时间线交互修复的既定纪律）
    $("catalog-sections").addEventListener("click", function (e) {
      var btn = e.target.closest("button[data-action]");
      if (btn) { handleAction(btn.getAttribute("data-action"), btn.getAttribute("data-section")); }
    });
  }

  window.CatalogUI = { open: open, init: init };
})();
