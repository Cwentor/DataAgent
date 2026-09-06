# 评审报告｜深度审计：AuthenticationError 空根遗留风险项核实（2026-09）

> 评审日期：2026-09-06 · 评审方式：全量代码检索 + git 历史取证 + 路由门禁走读 · 评审范围：`auth/`、`web/server.py`、`web/service.py`

---

## 1. 立项背景

[`20260906-repair-chatbi-domain-specificity.md`](20260906-repair-chatbi-domain-specificity.md) §四 残余风险说明中列有一项 P0：

> | `AuthenticationError` 空根 | `web/service.py:45` 分支 `raise AuthenticationError()` 仍存在；路由不阻断 | P0 | 整改指令1 范围外，建议单独立项修复 |

按"单独立项"要求，本文档作为该立项的核实记录与闭环依据。

## 2. 核实证据

### 2.1 空根调用不存在

- 全代码库正则检索 `raise [A-Za-z]+\(\)`（空参异常构造）：**0 处命中**；
- `AuthenticationError` 全部 7 处构造均携带明确错误消息：

| 位置 | 消息 |
| --- | --- |
| `auth/identity.py:142` | `用户不存在: {username!r}` |
| `auth/identity.py:144` | `用户已停用: {username!r}` |
| `auth/identity.py:151` | `用户名或口令错误` |
| `auth/gateway.py:75` | `缺少令牌` |
| `auth/gateway.py:84` | `令牌校验失败: ...` |
| `auth/gateway.py:87` | `令牌缺少主体标识` |
| `auth/gateway.py:102` | `会话无效或已过期` |
| `auth/gateway.py:147` | `缺少身份凭证（需要 Bearer 令牌或会话）` |

### 2.2 报告所指行号系漂移误报

- `git log -S "AuthenticationError" -- web/service.py` **无任何输出**：`web/service.py` 全历史从未包含该标识符；
- 整改提交 `5d03bf4` 时点的 `web/service.py:45` 实际内容为 import 行 `from exec.pool import ReadOnlyConnectionPool`，无 `raise` 分支；
- 判断：报告撰写时引用了错误的文件/行号，"空根 `raise AuthenticationError()`"在仓库任意时点均不存在。

### 2.3 "路由不阻断"不成立

`web/server.py` 路由认证门禁走读（当前代码）：

| 端点 | 门禁 |
| --- | --- |
| `GET /api/health` | 公开（健康探活，设计预期） |
| `POST /api/auth/login` | 公开（登录入口，设计预期） |
| `/`、`/index.html`、`/static/*` | 公开（静态资源，设计预期） |
| `POST /api/query` | ✅ `_authenticate()` 失败返回 401（server.py:264-266） |
| `GET /api/metrics` | ✅ 同上（server.py:158-160） |
| `GET /api/export/<id>` | ✅ 同上（server.py:309-311） |
| `POST /api/auth/logout` | ✅ 同上（server.py:167-169） |
| `GET /api/auth/me` | ✅ 同上（server.py:142-144） |

受保护端点全部经 `authenticate(headers)` 解析身份，`AuthenticationError` 被捕获记审计日志后返回 401，不存在"认证失败仍继续执行"的路径。

## 3. 结论与处置

- **结论**：该项为报告行号漂移所致的**误报**，当前代码库不存在空根 `AuthenticationError` 调用，也不存在认证门禁缺失路径，无需代码修复；
- **处置**：本立项以核实关闭；[`20260906-repair-chatbi-domain-specificity.md`](20260906-repair-chatbi-domain-specificity.md) §四 已同步更新该项及同节其余两项（KV 缓存容量配置化、SQLite WAL/busy_timeout）的处置状态；
- **遗留动作**：无。
