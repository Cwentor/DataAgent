/* DataAgent 独立登录页逻辑：
 * - Auth Guard：进入页面先校验既有凭证（Bearer/Session），已登录直接跳主控制台；
 * - 提交登录：成功后按「记住我」选择 Token 存储位置，带回 redirect_url；
 * - 记住我：勾选时持久化用户名（记住身份），永不持久化密码。
 */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var TOKEN_KEY = "dataagent_token";
  var SESSION_KEY = "dataagent_session";
  var REMEMBER_KEY = "dataagent_remember_user";
  var CONSOLE_PATH = "/";

  function storage(getter) {
    return function (key, value) {
      try {
        if (arguments.length === 1) { return getter(key) || ""; }
        if (value === null) { getter(key, ""); } else { getter(key, value); }
      } catch (e) { /* 隐私模式等场景忽略 */ }
      return "";
    };
  }
  var localGet = storage(function (k) { return localStorage.getItem(k); });
  var localSet = storage(function (k, v) { localStorage.setItem(k, v); });
  var sessionGet = storage(function (k) { return sessionStorage.getItem(k); });
  var sessionSet = storage(function (k, v) { sessionStorage.setItem(k, v); });

  function getToken() { return sessionGet(TOKEN_KEY) || localGet(TOKEN_KEY); }

  function redirectTo(url) {
    window.location.replace(url);
  }

  function safeRedirectUrl() {
    // 仅允许站内相对路径，防开放重定向
    var raw = new URLSearchParams(window.location.search).get("redirect_url") || CONSOLE_PATH;
    if (raw.charAt(0) !== "/" || raw.charAt(1) === "/") { return CONSOLE_PATH; }
    return raw;
  }

  function showAlert(msg) {
    var el = $("login-alert");
    el.textContent = "⚠ " + msg;
    el.classList.remove("hidden");
  }
  function hideAlert() {
    $("login-alert").classList.add("hidden");
    $("login-alert").textContent = "";
  }

  function setLoading(on) {
    var btn = $("login-btn");
    btn.disabled = on;
    btn.classList.toggle("loading", on);
    var text = btn.querySelector(".submit-text");
    if (text) { text.textContent = on ? "登录中…" : "登 录"; }
  }

  function fillRememberedUser() {
    var saved = localGet(REMEMBER_KEY);
    if (saved) {
      $("username").value = saved;
      $("remember").checked = true;
      $("password").focus();
    }
  }

  function submitLogin() {
    var username = $("username").value.trim();
    var password = $("password").value;
    if (!username || !password) {
      showAlert("请输入用户名与密码");
      return;
    }
    hideAlert();
    setLoading(true);
    fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: username, password: password })
    })
      .then(function (r) {
        return r.json().then(function (data) { return { status: r.status, data: data }; });
      })
      .then(function (res) {
        if (res.status === 200 && res.data.token) {
          var remember = $("remember").checked;
          // Token 默认放 sessionStorage（标签页关闭即失效）；勾选「记住我」升级为
          // localStorage（浏览器重启保持登录）。密码永不持久化。
          if (remember) {
            localSet(TOKEN_KEY, res.data.token);
            if (res.data.session_id) { localSet(SESSION_KEY, res.data.session_id); }
            localSet(REMEMBER_KEY, username);
          } else {
            sessionSet(TOKEN_KEY, res.data.token);
            if (res.data.session_id) { sessionSet(SESSION_KEY, res.data.session_id); }
            localSet(REMEMBER_KEY, null);
          }
          redirectTo(safeRedirectUrl());
          return;
        }
        if (res.status === 429) {
          var retry = res.data && res.data.retry_after;
          showAlert("尝试过于频繁，请 " + (retry ? retry + " 秒后" : "稍后") + "再试");
        } else {
          showAlert((res.data && res.data.error) || "登录失败，请稍后再试");
        }
        setLoading(false);
      })
      .catch(function () {
        showAlert("登录请求失败，请检查网络后重试");
        setLoading(false);
      });
  }

  function showLogoutReason() {
    try {
      var reason = sessionStorage.getItem("dataagent_logout_reason");
      if (reason) {
        showAlert(reason);
        sessionStorage.removeItem("dataagent_logout_reason");
      }
    } catch (e) { /* 忽略 */ }
  }

  // ---------------------------------------------------------------- 初始化
  showLogoutReason();

  $("pw-eye").addEventListener("click", function () {
    var input = $("password");
    var show = input.type === "password";
    input.type = show ? "text" : "password";
    this.setAttribute("aria-pressed", show ? "true" : "false");
    this.textContent = show ? "🙈" : "👁";
  });

  $("login-form").addEventListener("submit", function (e) {
    e.preventDefault();
    if (!$("login-btn").disabled) { submitLogin(); }
  });

  // Auth Guard：已登录（含服务端会话 Cookie）访问 /login 直接回主控制台。
  // /api/auth/me 是轻量身份校验端点，不属于业务/配置 API，允许在登录页调用。
  fetch("/api/auth/me", { headers: getToken() ? { Authorization: "Bearer " + getToken() } : {} })
    .then(function (r) {
      if (r.ok) { redirectTo(safeRedirectUrl()); return null; }
      fillRememberedUser();
      return null;
    })
    .catch(function () { fillRememberedUser(); });
})();
