# 任务跟踪｜Hive 执行后端支持（二十五期）

> 配套文档：[设计方案](../designs/2026-10-27-hive-backend-design.md) ·
> [实施计划](../plans/2026-10-27-hive-backend-plan.md)
> 分支：`feat/hive-backend` · 基线：master `a006c39` · 开工日期：2026-10-27

## 任务清单

- [x] T0 计划期实测裁决（apache/hive:4.0.0 本地容器，结论回写 spec §0）
- [x] T1 配置与依赖基座
- [x] T2 方言层 HiveRenderer
- [x] T3 引擎层 HiveAdapter
- [x] T4 护栏方言黑名单
- [x] T5 探索层内联派生表扩展
- [x] T6 建仓链路（schema_hive + init_hive + build 分支）
- [x] T7 parity 测试汇聚
- [x] T8 eval 冒烟
- [x] T9 CI hive-parity 门禁 + AGENTS.md 契约小节
- [ ] T10 全量收尾（质量门 + Review Focus 核对 + CI 收敛 + 提交推送）

## Review Focus 专项核对表

（失效模式定义见 plan）

- [x] RF-1 `\\N` null 标记转义链路：HiveAdapter.CSV_NULL_MARK 单测字节锁定 +
      schema 对拍快照断言 `NULL DEFINED AS '\\N'`（tests/engine/test_hive_adapter.py、
      tests/mock/test_schema_hive.py）
- [x] RF-2 LOAD DATA staging 双路径语义：insert_dataframe 单测断言 LOAD 语句含
      server 路径；CI 卷挂载 client/server 同内容（ci.yml hive-parity）
- [x] RF-3 posexplode spine 桶序/别名语义：方言单测桶数边界（day 1826 / week
      归一 / quarter / 月末）+ 本地容器 parity（fill_gaps 1826 行双后端一致实测通过）
- [x] RF-4 DESCRIBE 解析器分区段 fail-closed：parse_describe_rows 单测
- [x] RF-5 HiveQL 特有语句族黑名单逐词锁定：guards 黑名单 hive 键
      （DFS/MSCK/USE/SHOW/DESCRIBE/LOCK/UNLOCK/GRANT/REVOKE/ANALYZE/ARCHIVE/
      UNARCHIVE/KILL/ABORT/MERGE + reflect/java_method/system: 函数族）

## 执行日志

### 2026-10-27 计划期（T0 实测裁决）

- 本地起 `apache/hive:4.0.0` 单容器（SERVICE_NAME=hiveserver2 + 内嵌 Derby），
  beeline 实测：`date_trunc` 不存在；`trunc(ts,'MM'/'Q'/'YEAR')` 三档可用且
  接受 timestamp；`pmod(datediff(ts,'1970-01-05'),7)` 周一锚定成立（周日=6）；
  `WITH spine AS (... LATERAL VIEW posexplode(split(space(n),' ')) ...)` 实测
  产出 0..n；`add_months('2025-01-31',1)=2025-02-28` 月末钳制正确；HAVING
  别名可用；DECIMAL 除法高精度 + NULLIF 可用。
- 灌数通道实测：CSV + `LOAD DATA LOCAL INPATH`（服务端本地路径语义）打通；
  发现 null 标记转义坑——Hive SQL 字面量 `'\N'` 落库 `serialization.null.format='N'`
  （反斜杠被字面量解析吃掉），须写 `'\\N'`（双反斜杠）；CSV 中 `\N` 正确
  解析为 NULL（is_null=true 实测）。观察项：Hive 4 managed 表默认
  `TRANSLATED_TO_EXTERNAL` + `external.table.purge=TRUE`；warehouse 走本地
  file:// 无 HDFS。
- 驱动实测：PyHive 0.7.0 + thrift 0.25.0 在 Windows dataagent 环境纯 Python
  安装成功（无 sasl 编译依赖）。
- 本机环境事实：Docker Desktop 29.8.0；conda 环境 `dataagent`；Git Bash 的
  MSYS 参数转换与工具调用 JSON 层会各吃一层反斜杠——测试用 SQL 文件 + 
  `chr(92)` 生成方式规避（生产代码不受影响，转义口径以 Python 字符串显式写法
  锁定并单测）。

### 2026-10-27 实施期（T1-T9，同日完成）

- **T0 补充实测（HTTP transport，M10 收口）**：PyHive `scheme="http"` 走
  THttpClient（URI 固定 /cliservice/）连通实测通过；读超时经
  `conn._transport.setTimeout` 设置。容器踩坑三件（全部回写 spec §0/CI 注释）：
  ① `/tmp/hive` 权限——Hadoop 权限检查失败进 60s 重试循环，`chmod 1777` 后
  下一轮成功（CI 启动后立即处理）；② 挂载 hive-site.xml 是**整体替换**，仅写
  transport 四项会丢 warehouse.dir 导致建表失败——入库配置为镜像默认 13 项 +
  transport 4 项合并版；③ `docker restart` 后 pid 文件残留导致 entrypoint
  退出，须重建容器。
- **实施期新实测坑（全部单测/CI 固化）**：
  ① **Hive 裸标识符禁下划线开头**——`__spine` CTE 名 / `__one` LATERAL VIEW
  别名 / `_field_metadata` 表名均 ParseException；反引号包裹后合法。编译器
  内部别名经 `renderer.internal_aliases()` 覆盖点方言化（getattr 分派，四方言
  缺省裸名产物逐字节不变）；adapter/init 的元数据表 DDL 显式反引号。
  ② **`_` 开头 staging 文件被 Hive LOAD 跳过**（Hadoop 隐藏文件约定）——
  `_field_metadata.csv` 实锤 "No files matching path"；staging 文件名统一
  `data_` 前缀。
  ③ **PyHive DATE 列原样返回字符串**（TYPES_CONVERTER 仅 DECIMAL/TIMESTAMP）
  ——adapter.run_query 按 description 类型定向补 date 转换（hive_cell）。
  ④ Windows Docker Desktop bind mount 覆盖写传播抖动——insert_dataframe 对
  "No files matching path" 做 3 次退避重试（Linux CI 原生挂载无此问题）。
- **T6 建仓端到端（本地容器实测）**：`python -m mock.init_hive` 全绿，24 张
  业务表行数与 init_duckdb 逐表一致（fact_display 467057 / order_detail
  15019 / user_info 3083 等）；种子表通道并入 insert_dataframe（Hive 无
  executemany 可用语义）。
- **T7 parity（本地容器实测）**：周边界 / fill_gaps 1826 行 / comparison /
  扫描预检 warning 留痕 / 池复用身份回读 5 用例全绿；golden 全量 DSL parity
  本地跑通（见下方验证清单）。
- **T8 eval 冒烟实测**：`eval --backend hive` 行数 15019，治理链路整链路通。
- **T7 收尾**：`tests/test_catalog_backend_parity.py` 增 hive 条目（DESCRIBE
  实报小写形态），目录一致性测试 5 后端全绿。

## 服务级验证清单

**本地验证（2026-10-27，apache/hive:4.0.0 单容器 @127.0.0.1:10001）**：

- [x] `python -m mock.init_hive` 建仓成功，24 表行数与 init_duckdb 一致；
- [x] `python -m pytest tests/test_hive_backend_parity.py -q` 全绿
      （golden 全量 DSL + Review Focus 专项，真实容器执行）；
- [x] `python -m eval.eval_runner --backend hive` 冒烟通过（15019 行）；
- [x] `python -m semantic.catalog_loader`（DB_BACKEND=hive）目录构建通过；
- [x] 本地 `black --check .` / `ruff check .` / `python -m pytest -q` 全绿
      （1357 passed / 24 skipped）；
- [x] golden oracle 31 例全 PASS（编译器 internal_aliases 改动零破坏）。

**CI 验证（PR 首跑后回填：run 号 / job / 耗时 / 轮次）**：

- [ ] hive-parity job 首绿（Hive 链路 CI 首次真实验证）；
- [ ] lint / test / mysql-parity / postgres-parity / clickhouse-parity 不变绿。
