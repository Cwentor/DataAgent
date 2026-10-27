# 实施计划｜Hive 执行后端支持（二十五期）

> **Goal**：`DB_BACKEND` 五值（+hive），PyHive HTTP transport 直连 HiveServer2，
> 双轨（adapter/renderer）接入 + 建仓（CSV staging + LOAD DATA）+ parity/CI 门禁。
> **Architecture**：`exec/engine/hive_adapter.py` + `compiler/dialect/hive.py`，
> 注册表各一行，护栏/探索层方言集合扩展，消费方零改动。
> **Tech Stack**：PyHive 0.7 + thrift（纯 Python，三平台统一）；apache/hive:4.0.0
> 单容器（HiveServer2 + 内嵌 Derby + HTTP transport）。
> **Spec**：[../designs/2026-10-27-hive-backend-design.md](../designs/2026-10-27-hive-backend-design.md)（执行者需同时读两者；D1-D13 决策与 §0 实测纪要是任务口径的唯一来源）。

## 计划期实测裁决更新

- M1-M10 见 spec §0（本地 apache/hive:4.0.0 容器实测）：date_trunc 不存在改
  trunc 等价式；spine 走 posexplode CTE；灌数走 CSV staging + LOAD DATA；
  null 标记 SQL 字面量双反斜杠；HAVING 别名可用；PyHive Windows 安装通过。
- PyHive HTTP transport 连通实测：见 tasks 执行日志（回填）。

## Global Constraints（全局红线）

1. golden 锚定 DuckDB 方言，`eval/rebuild_golden.py` 与 golden_dataset.json
   **零改动**；既有四 renderer/四 adapter 编译产物逐字节不变；
2. `semantic/catalog_loader.py` **严禁**新增任何 if backend 分支（AGENTS.md
   铁律）；Hive 物理元数据只经 `HiveAdapter.physical_columns`；
3. `mock/gmall/schema.py` 是建表 DDL 唯一来源，`schema_hive.py` 只做运行时
   转译 + 后处理，**严禁手写第二份表结构**；
4. `build_gmall_tables` 必须给 hive 显式分支（现 else 兜底会把 MySQL DDL 静默
   打到 Hive，非 fail-closed）；
5. 新代码 docstring/注释简体中文、测试函数名英文；提交前 black/ruff/pytest
   全绿；
6. LLM 产出的 SQL 永不直接执行；Hive 方言黑名单按 D4/T4 口径圈定，禁止照抄
   其他引擎清单。

## Review Focus（规格隐含、无直接测试覆盖的失效模式 Top 5）

| # | 失效模式 | 归属任务 |
|---|---|---|
| 1 | `\\N` null 标记在 Python→Thrift 字面量解析层再吃一次反斜杠，CSV NULL 静默变字面量 `\N`（parity 数值表或许巧合通过、字符串 NULL 全错） | T6/T7（insert 单测 + parity NULL 行） |
| 2 | `LOAD DATA LOCAL INPATH` 的 LOCAL 是 **hiveserver2 进程本地路径**，误用 client 路径在 CI（双容器视角）报 FileNotFound | T6/T9（staging 卷挂载同路径断言） |
| 3 | posexplode spine 与主查询 join 时桶序/去重语义漂移（LATERAL VIEW 输出列未显式别名） | T2/T7（spine 单测 + fill_gaps parity） |
| 4 | DESCRIBE 解析器把分区段/元数据行当普通列（未来若有人给数仓加分区，目录静默脏化） | T3（fail-closed 解析单测） |
| 5 | 方言黑名单漏 `LOAD`/`DFS` 等 HiveQL 特有语句族（探索 SQL 提升面直接写 HDFS/warehouse） | T4（黑名单单测逐词锁定） |

## Tasks

- [x] **T1 配置与依赖基座**：`config/settings.py` HIVE_* 五件套 + STAGING 两路径
      + DB_BACKEND 五值注释；`requirements.txt` 驱动注释段（选型理由/否决项/上界
      pin/升级跟随）；`.env.example` 增量；`exec/engine/__init__.py` 与
      `compiler/dialect/__init__.py` 注册表各一行（T2/T3 文件随后落地，先建注册
      防环）。
- [x] **T2 方言层**：`compiler/dialect/hive.py` HiveRenderer 十原语（D6 口径逐条
      实现；spine 用 posexplode CTE + 纯 Python 桶数；INTERVAL 转译照
      pg_interval_literal 先例）+ `tests/compiler/dialect/` 单测（渲染快照 +
      CompileError 分支 + 既有四方言快照不变）。
- [x] **T3 引擎层**：`exec/engine/hive_adapter.py` HiveAdapter 全协议（D2/D3/D4/
      D5/D10/D11）+ `tests/engine/test_hive_adapter.py` 纯逻辑单测（硬编码清单/
      错误归一/DESCRIBE 解析器/CSV 写出字节，用假驱动对象注入）。
- [x] **T4 护栏方言黑名单**：`exec/guards.py` `_DIALECT_EXTRA_KEYWORDS["hive"]`、
      `_DIALECT_EXTRA_FUNC_RE["hive"]`、Lock 分支评估；单测逐词锁定。
- [x] **T5 探索层**：`core/retrieval/exploration.py` 内联派生表方言集合加
      `"hive"`；相关既有测试跑绿。
- [x] **T6 建仓链路**：`mock/gmall/schema_hive.py`（sqlglot 转译 + 后处理 D9）+
      `mock/init_hive.py`（照 init_clickhouse 骨架：显式注入 adapter、DROP 幂等、
      行数打印）+ `mock/gmall/build.py` hive 显式分支（Global Constraint 4）+
      `tests/mock/test_schema_hive.py` 对拍。
- [x] **T7 parity 测试**：`tests/test_hive_backend_parity.py`（HIVE_TEST_* 门控，
      场景照 CH 模板裁剪：golden DSL 全量 / 周边界 / comparison / fill_gaps（D7
      裁决） / 连接池复用只读验证口径 per-backend / 扫描熔断专项改为"warning
      诚实留痕"断言）；`tests/test_catalog_backend_parity.py` 加 hive 条目。
- [x] **T8 eval 冒烟**：`eval/eval_runner.py` `_hive_smoke()` + choices 加
      `"hive"`（照三先例 fail-fast）。
- [x] **T9 CI 门禁**：`.github/workflows/ci.yml` 加 `hive-parity` job（D13：卷
      挂载 conf + staging、beeline 就绪循环、CREATE DATABASE、init、parity）；
      AGENTS.md 增 Hive 契约小节。
- [x] **T10 全量收尾**：本地三件套（black/ruff/pytest）全绿；golden 31 例回归；
      push 分支触发 CI 首跑，收敛 hive-parity（轮次记录回填 tasks）；Review
      Focus 逐条核对；tasks 收口。
