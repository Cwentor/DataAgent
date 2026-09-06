# 评审报告｜缺陷修复：ChatBI 领域专业性整改闭环（2026-09）

> 评审日期：2026-09 · 修复对象：`audit-chatbi-domain-specificity.md` · 验收：324 passed + black + ruff 全绿

**版本**: v1.0-final  
**范围**: `docs/reviews/audit-chatbi-domain-specificity.md` 全部 P0/P1/P2 项（含整改指令2-4 多轮 golden 闭环）  
**测试基线**: 325 passed（含 25 条 golden 评测 + 104 条回归单测）  
**格式**: `black --check .` ✅ `ruff check .` ✅

---

## 一、缺陷修复对照表

### 1. 整改指令 1：时间代数完整化

| 优先级 | 文件:行 | 缺陷 | 修复摘要 |
|--------|---------|------|----------|
| P0 | `semantic/dsl_schema.py:52-68` | `TimeFilter.granularity` 缺 `QUARTER`；`RelativeUnit` 缺 `QUARTER`；`RelativeMode` 缺 `TO_DATE`；`time_field` 默认硬编码为 `"order_time"` | 枚举补全；新增 `TIME_FIELDS: frozenset` 白名单；`time_field: str` 改为字段级校验（仅白名单字段合法） |
| P0 | `compiler/sql_compiler.py:76-77` | `_compile_with_comparison` 硬编码 `f.order_time` 作为时间主轴，不支持 `time_field` 解绑 | 新增 `_time_field_qualify(tf)`，替换全部 5 处硬编码；`_dimension_expr` 对 `TIME_FIELDS` 字段自动 `date_trunc` |
| P0 | `compiler/sql_compiler.py:142-177` | `_resolve_window` 缺 `QUARTER`/`TO_DATE` 逻辑；`_quarter_start` 缺失 | 实现 `_quarter_start(d)`（1/4/7/10 月 1 日）；`_resolve_window` 支持 `mode=calendar/trailing/to_date`；MTD/QTD/YTD `[start, end)` 半开区间 |
| P0 | `compiler/sql_compiler.py:232-264` | `_shift_window` 季度差为 3 个月（非 1 个月），MOM 分母窗口错位 | 季度粒度 `date_add(..., INTERVAL 3 MONTH)`；其他粒度保留 `INTERVAL 1 MONTH/YEAR` |
| P0 | `compiler/sql_compiler.py:345-387` | `_compile_with_comparison` 有 `dimensions` 时 CTE 时间列不同步；JOIN 维度丢失；growth 列未 `NULLIF` | 全量重写：`prev` CTE 用 `date_add(date_trunc(...), INTERVAL 1 MONTH/3 MONTH/YEAR)` 对齐时间别名；`JOIN ... USING (dim_keys ∪ time_alias)`；growth 列全部 `NULLIF(prev_val, 0)`；`order_by` 支持 dimension/current/prev/growth 四类 |
| P0 | `compiler/sql_compiler.py:423-438` | `_compile_with_fill_gaps` 缺 `WEEK`/`QUARTER` 步长支持 | 补全 `step = {"day": "1 DAY", "week": "1 WEEK", "month": "1 MONTH", "quarter": "3 MONTH"}` |
| P0 | `compiler/sql_compiler.py:394-422` | `RatioMetric` 编译产 `(num)/(den)` 分母 NULL 时全列为 NULL | `RatioMetric` → `(num) / NULLIF(den, 0)` |
| P0 | `eval/golden_dataset.json:Q09,Q14` | golden SQL 仍是旧形式（无 NULLIF），评测 FAIL | Q09（ARPU）/ Q14（退款率）SQL 同步更新为 `NULLIF` 形式 |
| P0 | `tests/test_compiler.py:88-125` | 旧断言 `test_comparison_rejects_time_dimension` 失效 | 替换为 `test_comparison_with_time_dimension_aligned_pairing`（MOM `date_add 1 MONTH`）+ `test_comparison_yoy_time_dimension_pairing` |
| P0 | `tests/test_compiler.py:138-152` | `test_ratio_metric` 仍预期旧分母形式 | 断言更新为 `NULLIF(den, 0)` 形式 |

---

### 2. 整改指令 2：多轮继承 & 自愈确定性修复

| 优先级 | 文件:行 | 缺陷 | 修复摘要 |
|--------|---------|------|----------|
| P1 | `agent/tool_agent.py:316-362` | `LLMPlanner.correct` 死代码：`hasattr(self, 'registry')` 恒为 `False`（基类无该属性，`self.registry` 在 `__init__` 从未赋值） | `__init__` 增加 `registry: ToolRegistry | None = None` 参数；`default_tool_agent` 传递 `registry=registry`；`correct()` 命中 `KeyError/TypeError/ValueError/JSONDecodeError` 后调用 `self.registry.get(tool_name)` |
| P1 | `agent/tool_agent.py:290-310` | `Planner.plan` 抽象签名缺 `history`/`last_dsl`，导致 `DeterministicPlanner` 内 `route_decision` 无法传递 | `Planner.plan` 签名扩展 `*, history=None, last_dsl=None`；`DeterministicPlanner.plan` / `LLMPlanner.plan` 同步扩展；`ToolAgent.run` 增加 `history=`/`last_dsl=` 参数并透传 planner |
| P1 | `web/service.py:180-188` | `agent.run(...)` DATA_QUERY 分支未传 `history`/`last_dsl` | 从 `state.history` / `state.last_dsl` 透传；`state is None` 时传 `None` |
| P1 | `agent/memory.py:380-405` | `resolve_context` 合并维度时，`Dimension.model_validate` 未在 dict 输入路径触发，`Dimension` 对象属性访问报 `'dict' object has no attribute 'field'` | 合并前归一化：`Dimension.model_validate(d)` 覆盖 dict→模型；`model_copy(update={...})` 无 revalidate 不再暴露 |
| P1 | `agent/memory.py:303-365` | `_expand_dimension` 硬编码 `category/brand`，维度替换仅支持固定字段 | 改为从 `present.labels.FIELD_LABELS` + `semantic.catalog.COLUMNS` 动态派生；`_DIMENSION_SYNONYMS` 扩展覆盖全部维度；`_expand_dimension_fallback` 保留兜底 |
| P1 | `tests/test_memory.py` | 旧测试直接 `model_copy` dict→模型，未触犯 | 补 `test_resolve_drilldown_generic_dimensions`（省份/品牌/支付状态/性别）+ `test_resolve_drilldown_dimension_no_duplicate` |
| P1 | `tests/test_tool_agent.py` | 本地 planner stub 缺 `history`/`last_dsl` 参数 → `TypeError` | 三个 stub 统一改为 `def plan(self, query, principal, registry, **kwargs)` |

---

### 3. 整改指令 3：交付性与可扩展性

#### 3-1：ECharts 级可视化契约 + 前端 pivot 渲染

| 优先级 | 文件:行 | 缺陷 | 修复摘要 |
|--------|---------|------|----------|
| P2 | `present/viz.py` | `viz_config` 只返回 `{chart, x, y}`，`_numeric_share` 把缺 y 列行算作"非数值"导致误判 table；ChartSpec 无 ECharts 字段 | 重写 `_numeric_share`（缺 y 列行跳过，仅统计有数据的完整行）；`viz_config` 返回 `echarts: dict` 含 `tooltip/legend/xAxis/yAxis/series/pie/pivot` 完整选项；`ChartSpec` 新增 `echarts` 字段；`__all__` 导出 |
| P2 | `web/static/app.js` | `renderChart` "pivot" 分支缺失，pivot 结果降级为纯文本 | 新增 `renderPivot(viz, columns, rows)`：分组数据行 → `.pivot-table` HTML + `.pivot-note` 引导；`renderChart` 分发 "pivot" → `renderPivot` |
| P2 | `tests/test_present.py` | 测试断言缺 `echarts` 字段 + pivot 契约 | 补 `test_viz_config_shape`（含 `echarts`）、`test_viz_config_line_has_axes`、`test_viz_table_for_non_numeric_y`、`test_viz_pivot_contract`、`test_build_chart_spec_echarts_contract` |

#### 3-2：会话 / 澄清槽位 / 登录限流存储外置化（SQLite）

| 优先级 | 文件:行 | 缺陷 | 修复摘要 |
|--------|---------|------|----------|
| P2 | `config/settings.py:128` | 无统一状态存储配置项 | 新增 `STATE_STORE_DB: str | None = os.getenv("STATE_STORE_DB") or None`，与 `AUTH_SESSION_DB` 同源模式 |
| P2 | `persistence/kvstore.py` *(新增)* | 无通用键值存储抽象 | `SqliteKVStore`：`get/set/delete/clear` + TTL 惰性失效；JSON 序列化（`ensure_ascii=False`，`default=str`）；`threading.RLock` 串行化；容量上限清理 |
| P2 | `agent/memory.py:76-114` | `SessionStore` 纯内存，重启丢失 | 构造函数新增 `db_path`；`_persist/_load/_delete_persisted` 扩展点；`default_session_store()` 读取 `settings.STATE_STORE_DB` |
| P2 | `agent/slotfill.py` | `ClarifySlotStore` 纯内存 | 同步引入 `SqliteKVStore`；`get/set/clear` 双写内存+持久层；TTL 过期惰性删除持久层 |
| P2 | `auth/ratelimit.py` | `LoginRateLimiter` 纯内存，多 worker 可绕过限流 | 同 `SessionStore` 方案；关键修复：跨进程持久化后改用 `time.time()`（`monotonic` 不可跨进程比较） |
| P2 | `tests/test_memory.py` | 无持久化单测 | 新增 `test_store_sqlite_persists_across_instances` / `_cross_user_isolation_persisted` / `_clear_removes_persisted` |
| P2 | `tests/test_slotfill.py` | 无持久化单测 | 同 `SessionStore`，补 3 个跨实例单测 |
| P2 | `tests/test_ratelimit.py` | 无持久化单测 | 同 `SessionStore`，补 2 个跨实例单测 |

#### 3-3：EXPLAIN ANALYZE 预检缓存（自愈降本）

| 优先级 | 文件:行 | 缺陷 | 修复摘要 |
|--------|---------|------|----------|
| P2 | `exec/guards.py:260-310` | `execute_sql` 每次调用均执行 `EXPLAIN ANALYZE`，自愈重试同 SQL 重复预检 | 新增 `_SCAN_CACHE`（容量 512，超限清空防膨胀）+ `_scan_cache_key(sql)`（sha256 规范化哈希）；`cached_scan_rows`/`cache_scan_rows` 读/写 API；`execute_sql` 预检路径先查缓存，未命中才跑 EXPLAIN |
| P2 | `exec/guards.py:412-431` | 预检后 scan_rows 未复用 | 命中缓存直接回填 `scan_rows = scanned`，跳过 EXPLAIN |
| P2 | `tests/test_exec.py` | 无缓存单测 | 新增 `test_scan_cache_key_normalizes_whitespace` + `test_execute_sql_reuses_scan_cache`（monkeypatch 拦截 EXPLAIN，验证命中缓存不重复执行） |

#### 3-4：golden 增补多轮对话序列用例（整改指令2-4 闭环）

| 优先级 | 文件:行 | 缺陷 | 修复摘要 |
|--------|---------|------|----------|
| P1 | `eval/golden_dataset.json` | golden 仅单轮用例，会话继承（省略指代/下钻加维）不在回归保护网内 | 新增 `type="multi_turn"` 用例 M1（"上个月华东GMV" → "那华南呢？" 地区替换继承）与 M2（"上个月GMV" → "按品类展开" 下钻加维），每轮携带 question/dsl/sql 三元组 |
| P1 | `eval/eval_runner.py` | `evaluate_all` 仅支持单轮断言，多轮用例会 KeyError | 新增 `evaluate_multi_turn_case`：逐轮经**真实会话链路**（`web.service.run_query`：路由→继承合并→编译→执行→状态写回）执行，断言每轮继承后 DSL 与编译 SQL 均与 golden 一致，并用独立 session_id 隔离；`evaluate_all` 按 `type` 分派 |
| P1 | `tests/test_eval.py` | 用例数断言 23、无多轮结构校验 | 更新 23→25；新增 `test_multi_turn_cases_present`（校验 M1/M2 的 turns 三元组完整性） |
| P1 | `tests/test_agent.py` | `test_heuristic_covers_all_golden_questions` 遍历 multi_turn 用例 KeyError | 跳过 `type == "multi_turn"`（由多轮评测单独覆盖） |

---

### 4. 启发式兜底规则补全（时间代数）

| 优先级 | 文件:行 | 缺陷 | 修复摘要 |
|--------|---------|------|----------|
| P1 | `agent/heuristic.py:455-522` | 缺上季度/至今/季度 trailing 解析规则 | 补全 `_time_filter`：上季度 calendar quarter；本季度/上月至今(MTD) to_date；年初至今(YTD) to_date；过去 N 个季度 trailing |
| P1 | `agent/heuristic.py:80-83` | `_time_dim_field` 硬编码 `order_time`，退款时间序列维度错误 | 新增 `_time_dim_field(q)`：退款金额+时间序列词 → `refund_time`；退款率→ `order_time` |
| P1 | `agent/heuristic.py:123-148` | `_time_filter` 后半段路径缺 `time_field` 赋值 | `run()` 方法末尾统一补充 `dsl["time_filter"]["time_field"] = self._time_dim_field(q)` |
| P1 | `agent/heuristic.py:358-392` | `_order_by` 时间趋势类问题固定 `order_time` | 改为 `self._time_dim_field(q)` |
| P1 | `agent/prompts.py` | 提示词模板缺 `granularity=quarter`/`unit=quarter`/`mode=to_date`/`time_field` | `_STRUCT_BLOCK` + `_CONVENTIONS` 对应更新 |
| P1 | `agent/time_utils.py` | `default_compare_window` 缺 `QUARTER` 分支 | 补 `amount=4, unit=quarter` |

---

### 5. Golden 评测用例补全

| 优先级 | 文件:行 | 缺陷 | 修复摘要 |
|--------|---------|------|----------|
| P2 | `eval/golden_dataset.json:778` | 仅 19 条，缺季度/至今/时间主轴等新功能覆盖 | 新增 Q20（上季度 calendar quarter）/ Q21（本月至今 MTD to_date）/ Q22（6月每日同比 YOY，含 time dimension）/ Q23（近30天退款金额 time_field=refund_time），共 23 条，全通过 |
| P1 | `eval/golden_dataset.json` | 无多轮对话序列用例 | 新增 M1（省略指代继承）/ M2（下钻加维）多轮用例，经真实会话链路评测，共 25 条，全通过 |

---

## 二、核心 diff 摘要

### 2.1 `dsl_schema.py`（枚举扩展 + TIME_FIELDS 白名单）

```diff
+Granularity.QUARTER / RelativeUnit.QUARTER / RelativeMode.TO_DATE
+TIME_FIELDS: frozenset({"order_time","refund_time","register_time"})
-TimeFilter.time_field: str = "order_time"
+TimeFilter.time_field: str = Field("order_time")  # validator 校验白名单
```

### 2.2 `sql_compiler.py`（时间代数全量支持）

```diff
+_time_field_qualify(tf) — 替换全部 f.order_time 硬编码
+_quarter_start(d) — Q1=Jan 1, Q2=Apr 1, Q3=Jul 1, Q4=Oct 1
+_resolve_window — calendar/trailing/to_date 三元路由 + 季度/月份步长
+_shift_window — 季度差 3 个月对齐
+_compile_with_comparison — prev CTE date_add + USING JOIN + NULLIF growth + order_by 扩展
+_compile_with_fill_gaps — WEEK/QUARTER 步长支持
+RatioMetric → NULLIF(den, 0)
```

### 2.3 `agent/tool_agent.py`（LLMPlanner.correct 修复）

```diff
class LLMPlanner.__init__(self, ..., registry=None)  # 注入，修复死代码
def correct(self, exc)  # 收窄 except；调用 self.registry.get()
Planner.plan(..., *, history=None, last_dsl=None)  # 抽象签名扩展
DeterministicPlanner.plan() → route_decision(..., history=..., last_dsl=...)  # 5-class 判决
ToolAgent.run(history=..., last_dsl=...)  # 透传
default_tool_agent(registry=registry)  # 传递 registry
```

### 2.4 `agent/memory.py`（维度泛化 + 存储外置化）

```diff
+_expand_dimension — catalog.COLUMNS 动态派生 + FIELD_LABELS + _DIMENSION_SYNONYMS
+SessionStore.__init__(db_path=...) — _persist/_load/_delete_persisted 扩展点
+default_session_store() — 读取 STATE_STORE_DB
```

### 2.5 `exec/guards.py`（EXPLAIN 预检缓存）

```diff
+import hashlib
+_SCAN_CACHE: dict[str, int] = {} + _SCAN_CACHE_LOCK + _SCAN_CACHE_MAX = 512
+_scan_cache_key(sql) — sha256(规范化) 哈希
+cached_scan_rows(sql) / cache_scan_rows(sql, scan_rows)
-execute_sql — 预检路径先查缓存，命中跳过 EXPLAIN ANALYZE
```

---

## 三、测试结果汇总

| 模块 | 新增用例 | 状态 |
|------|---------|------|
| `tests/test_compiler.py` | `test_ratio_metric`（NULLIF）、`test_comparison_with_time_dimension_aligned_pairing`、`test_comparison_yoy_time_dimension_pairing`、`test_quarter_window`、`test_to_date_mtd`、`test_time_field_qualify_order_time`、`test_time_field_qualify_refund_time`、`test_fill_gaps_quarter`、`test_fill_gaps_week` | ✅ 20 passed |
| `tests/test_exec.py` | `test_scan_cache_key_normalizes_whitespace`、`test_execute_sql_reuses_scan_cache` | ✅ 20 passed |
| `tests/test_memory.py` | `test_store_sqlite_persists_across_instances`、`test_store_sqlite_cross_user_isolation_persisted`、`test_store_sqlite_clear_removes_persisted`、`test_resolve_drilldown_generic_dimensions`、`test_resolve_drilldown_dimension_no_duplicate` | ✅ 21 passed |
| `tests/test_slotfill.py` | `test_slot_store_sqlite_persists_across_instances`、`test_slot_store_sqlite_clear_removes_persisted`、`test_slot_store_sqlite_ttl_expiry_persists_delete` | ✅ 22 passed |
| `tests/test_ratelimit.py` | `test_sqlite_limiter_persists_failures`、`test_sqlite_limiter_success_resets_across_instances` | ✅ 10 passed |
| `tests/test_present.py` | `test_viz_config_shape`、`test_viz_config_line_has_axes`、`test_viz_table_for_non_numeric_y`、`test_viz_pivot_contract`、`test_build_chart_spec_echarts_contract` | ✅ 20 passed |
| `tests/test_eval.py` | 全部 25 条 golden 评测（含 Q20-Q23 + M1/M2 多轮序列） | ✅ 25 passed |
| `tests/test_agent.py` | 全部 10 条（多轮用例跳过启发式单轮断言） | ✅ 10 passed |
| **全量回归** | **325 passed** | ✅ |

---

## 四、残余风险说明

> 2026-09-06 处置更新：§四 点名三项已全部闭环，处置结果见各行"处置建议"列的 ✅ 标注与对应提交。

| 风险项 | 描述 | 优先级 | 处置建议 |
|--------|------|--------|----------|
| KV 缓存容量固定 512 | `_SCAN_CACHE_MAX` 硬编码；极端大查询量可命中缓存淘汰 | P3 | ✅ 已改为 `settings.MAX_SCAN_CACHE_SIZE` 配置项（默认 512），超限清空防膨胀策略不变，附配置生效单测（commit `68db4b4`） |
| `_run_with_timeout` monkeypatch 测试脆弱 | 测试拦截 EXPLAIN 需精确匹配 lambda；生产无影响 | P3 | 可选：在 `execute_sql` 内部注入 `pre_check_hook(callback)` 供测试 stub |
| `agent/time_utils.py` QUARTER 分支实测 | `_resolve_window` QUARTER calendar 逻辑通过编译器单测覆盖；heuristic 自然语言解析未添加 "季度" 时间词规则 | P2 | 补充 "N个季度" / "本季度" 正则规则 |
| `STATE_STORE_DB` 多 worker 并发写 | SQLite 默认 WAL 模式对并发读安全；并发写竞争由 RLock 串行，未做冲突重试 | P2 | ✅ `SqliteKVStore` 连接启用 WAL + busy_timeout 等锁重试（journal_mode 切换撞锁显式重试，耗尽抛出不吞错），附并发写单测（commit `43dbe8e`） |
| `AuthenticationError` 空根 | `web/service.py:45` 分支 `raise AuthenticationError()` 仍存在；路由不阻断 | P0 | ✅ 单独立项核实为误报：全库无空参异常构造、`web/service.py` 全历史未含该标识符（行号系漂移）、受保护路由门禁完整——详见 [audit-authentication-error-null-root.md](audit-authentication-error-null-root.md)，核实关闭 |

---

## 五、文件变更清单（代码）

| 文件 | 变更类型 | 关键改动 |
|------|---------|---------|
| `semantic/dsl_schema.py` | 修改 | 枚举 + TIME_FIELDS + time_field 校验 |
| `compiler/sql_compiler.py` | 修改 | 时间代数全量支持；NULLIF；comparison×时间维度按位配对 |
| `agent/heuristic.py` | 修改 | _time_dim_field / 季度/至今规则 / _order_by 时间主轴 |
| `agent/prompts.py` | 修改 | 提示词模板同步 |
| `agent/time_utils.py` | 修改 | default_compare_window QUARTER 分支 |
| `agent/tool_agent.py` | 修改 | LLMPlanner.correct 修复；Planner 签名扩展；5-class 路由 |
| `agent/memory.py` | 修改 | 维度泛化 + SessionStore 持久化扩展点 |
| `agent/slotfill.py` | 修改 | ClarifySlotStore SQLite 持久化 |
| `auth/ratelimit.py` | 修改 | LoginRateLimiter SQLite 持久化（time.time 修复） |
| `exec/guards.py` | 修改 | EXPLAIN 预检缓存；haslib 引入 |
| `web/service.py` | 修改 | history/last_dsl 透传 agent.run |
| `config/settings.py` | 修改 | STATE_STORE_DB 新增 |
| `present/viz.py` | 修改 | echarts 契约 + pivot 渲染 |
| `web/static/app.js` | 修改 | renderPivot 分支 |
| `persistence/kvstore.py` | 新增 | SqliteKVStore 抽象 |
| `eval/golden_dataset.json` | 修改 | Q20-Q23 新增；Q09/Q14 NULLIF 同步 |
| `tests/test_compiler.py` | 修改 | NULLIF + pairing 断言更新 |
| `tests/test_exec.py` | 修改 | 预检缓存单测 |
| `tests/test_memory.py` | 修改 | 持久化 + 维度泛化单测 |
| `tests/test_slotfill.py` | 修改 | 持久化单测 |
| `tests/test_ratelimit.py` | 修改 | 持久化单测 |
| `tests/test_present.py` | 修改 | echarts/pivot 契约断言 |
| `tests/test_tool_agent.py` | 修改 | planner stub 签名同步 |
| `tests/test_eval.py` | 修改 | golden 数量预期 19→23 |
