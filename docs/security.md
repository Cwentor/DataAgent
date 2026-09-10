# 安全模型

DataAgent 采用“生成前约束 + 生成后校验 + 执行层防护”的纵深安全模型。

## 身份认证

- 用户口令使用 PBKDF2-SHA256 哈希并进行恒定时间比对。
- JWT 使用 HS256，校验签名、过期时间、issuer、audience 与 not-before。
- Token 只携带用户名 `sub`，principal 与 role 不由客户端声明。
- Session 支持进程内存储，也可通过 `AUTH_SESSION_DB` 使用 SQLite 共享存储。
- 登录失败按用户名与 IP 进行指数退避限流。

## 数据权限

| 层级 | 机制 |
| --- | --- |
| 生成前 | `security/scope.py` 将主体可见字段与表子集注入 Prompt |
| 表级 | 引用表必须属于主体允许的表集合 |
| 列级 | 禁止访问主体不可见的敏感字段 |
| 行级 | 服务端强制注入 row filters（RLS） |
| 执行层 | 只读 SQL 白名单、超时、扫描行数与返回行数熔断 |

内置演示账号：

| 账号 | 口令 | 权限 |
| --- | --- | --- |
| `admin` | `admin123` | 全表 |
| `analyst` | `analyst123` | 全表，仅 5 省 RLS |
| `bob` | `bob123` | restricted：无退款表/敏感列，仅广东 |

> 演示账号仅用于本地开发与测试，生产部署必须替换密钥并使用真实身份管理。
