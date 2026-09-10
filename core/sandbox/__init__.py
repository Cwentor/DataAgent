"""sandbox 包：多租户安全代码解释器（Execution Plane）。

隔离模型（纵深防御，按强制强度递进）：
1. 静态层 ``ast_guard``：执行前 AST 解析，拦截危险 import / 危险调用
   （os、sys、subprocess、socket、pty、eval、exec、open、__import__、
   globals、locals 等），拒绝即不入执行器；
2. 运行时层 ``runner``：限权 builtins（白名单注入）、workspace 目录隔离
   （open 只能访问会话工作区）、stdout/输出大小上限、summary.json 结构化
   输出协议；
3. 进程层 ``backends``：可插拔执行后端——
   - DockerBackend：容器强隔离（--net=none、--cap-drop ALL、内存/CPU cgroups、
     非 root UID 10001、只读 rootfs），可用性运行时探测；
   - SubprocessBackend：本地子进程（默认），超时强杀 + 输出上限；POSIX 上
     以 resource.setrlimit 施加内存/CPU 限制，Windows 上如实标注
     ``limits_enforced=False``（不虚报隔离强度）。

数据交换协议（Data Exchange Protocol）：
- 上游 DSL 查询结果由 retrieval 层脱敏并导出 Parquet 到
  ``{workspace}/inputs/``；脚本经 pandas / polars / duckdb 只读消费
  （零网络、零数据库 socket）；
- 脚本只能写 ``{workspace}/outputs/``，产出 ``summary.json``（聚合矩阵
  ≤100 行）与可选 ``echarts_spec.json``。
"""
