# 实施计划｜语义目录 Web API（issue #27，2026-11）

设计依据：[docs/designs/2026-11-03-semantic-catalog-web-api-design.md](../designs/2026-11-03-semantic-catalog-web-api-design.md)
分支：`feat/issue27-semantic-catalog-api`（worktree：`.worktrees/issue27-semantic-catalog-api`）
范围：第一步 + 第二步一并实施（GET / 试校验 / PUT+PATCH 受控写入 / 前端面板）

---

## M1 semantic/catalog_loader.py：公开校验入口

1. 新增 `validate_overlay(overlay: dict, *, adapter=None) -> list[str]`：
   - 复用 `_build_from_overlay(overlay, physical)` 试构建，捕 `ValueError` 收集为错误串；
   - physical 经 `adapter.physical_columns()` 获取（缺省 `get_adapter()`；duckdb 库缺失时
     physical=None 走离线宽松语义，与 build_catalog 失败语义一致）；
   - `aliases` 附加自检：键必须属于 `fact_tables ∪ join_rules ∪ query_domains` 声明的表；
   - 返回结构化错误清单（空 = 合法），**不改任何全局状态**。
2. 不改动既有 `_read_overlay` / `build_catalog` / `refresh_catalog` 行为。

## M2 web/catalog_api.py：三个端点 + api.py 薄路由

1. 模块级常量：`EDITABLE_SECTIONS` / `READONLY_SECTIONS` / `IMPACT_HINTS`（设计 §4/§6）；
2. `catalog_version() -> str`：sha256(DEFAULT_OVERLAY_PATH 字节) 前 12 位；
3. `read_catalog() -> (200, body)`：文件不存在按运行态兜底（此时返回错误而非空目录——
   服务启动已 ensure_db + 校验过文件）；
4. `validate_catalog(body, *, user) -> (status, body)`：白名单校验 → 合并 →
   `validate_overlay` → diff 摘要 + impact 提示；不落盘；
5. `write_catalog(body, *, user, action) -> (status, body)`：
   - 模块级 `threading.Lock` 临界区：version 哈希校验（409）→ 白名单（400）→ 合并 →
     validate_overlay（400，结构化 errors）→ 同目录 `.tmp` 写入 + `os.replace` 原子替换 →
     `refresh_catalog()` → 返回新 version + diff + impact；
   - refresh 失败：写操作回滚（恢复原文件字节）并 500——目录与文件必须一致，
     严禁出现"文件已改、目录未刷"的中间态（自愈重试 refresh 一次后再失败才回滚）；
6. 权限：validate / write 要求 `admin` 角色（403），GET 登录即可；
7. api.py 挂路由：`GET /api/catalog`、`POST /api/catalog/validate`、
   `PUT /api/catalog`、`PATCH /api/catalog`（照 providers 模式 run_in_threadpool 包裹）。

## M3 审计扩展

1. `AuditRecord` 新增可选字段 `catalog_change: dict | None = None`；
2. `audit/store.py`：`_AUDIT_COLUMN_TYPES` 加 `catalog_change` 列（HUGEJSON 与 llm_usage
   同型）、`_INSERT_SQL` 补列、`_record_params` 补位置参数（单一映射点纪律）；
   `_migrate_schema` 幂等加列机制自动覆盖旧库；
3. 写入点：`write_catalog` 成功路径经 `write_audit_safely` 落
   `AuditRecord(prompt="语义目录变更", catalog_change={action, user, version_before,
   version_after, diff}, ...)`（AUDIT_ENABLED 关闭时跳过，与既有语义一致）。

## M4 前端管理面板

1. `web/static/js/catalog-ui.js`：分节卡片渲染（只读分组/可写分组）、JSON 编辑、
   试校验、保存（409 重拉提示）、影响面 toast；
2. `index.html` + `style.css`/`workspace.css`：入口按钮与面板骨架（对齐供应商面板结构）；
3. 侧栏知识目录面板（/api/schema/summary）不动。

## M5 测试与回归

1. `tests/web/test_catalog_api.py`（新文件）：见设计 §8 清单；
2. `tests/semantic/` 补 validate_overlay 用例；
3. `tests/audit/` 补 catalog_change 列迁移与双写；
4. CLI 链路回归：`python -m semantic.catalog_loader` 正常；
5. `python -m pytest -q` 全绿 + `black --check .` + `ruff check .`。

## M6 收尾

1. `docs/README.md` 登记设计与计划两篇索引；
2. issue #27 回贴实施摘要（合并后关闭）；
3. 提交（feat 规范）并推送分支，开 PR。

## 风险与回退

- semantic.json 是服务启动强依赖：写入路径校验前置 + 原子替换，崩溃窗口只留下
  `.tmp` 孤儿文件（启动时无害）；最坏情况 git checkout 恢复文件即可；
- 多 worker 部署（未来 uvicorn --workers >1）时进程内锁失效：当前单进程部署不受影响，
  设计注释中显式声明该边界。
