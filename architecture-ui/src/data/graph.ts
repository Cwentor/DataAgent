/**
 * DataAgent 架构图数据（唯一事实源）
 *
 * 分层与节点口径对齐 docs/guides/architecture.md 与 AGENTS.md：
 * 主链七层自上而下（交互 -> 编排 -> 智能 -> 语义与编译 -> 取数治理 -> 执行 -> 存储），
 * 另有两类旁挂组（横切支撑 / 质量保障）。改动节点或边后请同步核对文档。
 */

export type NodeKind = 'comp' | 'llm' | 'gate' | 'guard' | 'store' | 'ext'

export type Direction = 'TB' | 'LR'

export interface NodeSpec {
  id: string
  /** 节点主标题（中文） */
  label: string
  /** 代码路径副标题 */
  path?: string
  /** 关键机制一句话 */
  note?: string
  kind: NodeKind
}

export interface LayerSpec {
  id: string
  title: string
  subtitle?: string
  /** 旁挂组，与主链末层并排呈现 */
  side?: boolean
  nodes: NodeSpec[]
}

export interface EdgeSpec {
  source: string
  target: string
  label?: string
  dashed?: boolean
}

/** 节点类型图例元数据（配色中心定义） */
export const KIND_META: Record<NodeKind, { label: string; className: string }> = {
  comp: { label: '确定性组件', className: 'kind-comp' },
  llm: { label: '模型参与', className: 'kind-llm' },
  gate: { label: '审批门', className: 'kind-gate' },
  guard: { label: '治理护栏', className: 'kind-guard' },
  store: { label: '存储资产', className: 'kind-store' },
  ext: { label: '质量旁挂', className: 'kind-ext' },
}

export const LAYERS: LayerSpec[] = [
  {
    id: 'layer-web',
    title: '交互层',
    subtitle: 'web/',
    nodes: [
      { id: 'workbench', label: 'Agent 对话流工作台', path: 'web/static', note: '对话流时间线与产物视图', kind: 'comp' },
      { id: 'api', label: 'FastAPI 服务', path: 'web/api.py', note: '查询 / 编排 / 指标 / 导出端点', kind: 'comp' },
      { id: 'sse', label: 'SSE 流式端点', path: 'web/api.py', note: '九类事件实时推送', kind: 'comp' },
      { id: 'tasks', label: '异步任务', path: 'web/tasks.py', note: '后台执行与状态轮询', kind: 'comp' },
    ],
  },
  {
    id: 'layer-orchestrator',
    title: '编排层',
    subtitle: 'core/orchestrator/ · LangGraph 单引擎',
    nodes: [
      { id: 'clarify', label: 'clarify 澄清', path: 'nodes.py', note: '歧义检测与选项式反问', kind: 'comp' },
      { id: 'plan', label: 'plan 规划', path: 'nodes.py', note: 'LLM JSON 计划 + 启发式兜底', kind: 'llm' },
      { id: 'query', label: 'query 受控取数', path: 'nodes.py', note: '取数步骤调度', kind: 'comp' },
      { id: 'analyze', label: 'analyze 沙箱分析', path: 'nodes.py', note: '代码执行调度', kind: 'comp' },
      { id: 'critique', label: 'critique 反思', path: 'nodes.py', note: '三检 + 重规划 ≤3 次', kind: 'llm' },
      { id: 'synthesize', label: 'synthesize 综合报告', path: 'nodes.py', note: '四段式叙事 + 质检小节', kind: 'llm' },
      { id: 'autonomy', label: 'L1-L4 自主性分级', path: 'autonomy.py', note: 'maybe_interrupt 分流', kind: 'gate' },
      { id: 'gates', label: 'interrupt 审批门', path: 'langgraph_engine.py', note: 'clarify / plan_review / high_risk / exploration', kind: 'gate' },
      { id: 'fanout', label: 'Subagent fan-out', path: 'subagent.py', note: '受限任务卡 + 预算硬顶', kind: 'comp' },
    ],
  },
  {
    id: 'layer-agent',
    title: '智能层',
    subtitle: 'agent/ + core/skills/',
    nodes: [
      { id: 'intent', label: '意图路由', path: 'agent/router', note: '六分类 + 兜底准入制', kind: 'comp' },
      { id: 'nl2dsl', label: 'NL2DSL 双路径', path: 'agent/agent.py', note: 'LLM 严格校验 / 确定性启发式', kind: 'llm' },
      { id: 'heal', label: 'SQL 自愈重写', path: 'agent/pipeline.py', note: '报错清单喂回改写 DSL', kind: 'llm' },
      { id: 'memory', label: '多轮记忆', path: 'agent/memory.py', note: '指代继承与槽位回填', kind: 'comp' },
      { id: 'toolagent', label: 'Multi-Tool Agent', path: 'agent/tool_agent.py', note: '计划选择 / 执行守卫 / 反思', kind: 'comp' },
      { id: 'skills', label: '归因技能包', path: 'core/skills', note: '熵下钻 / 分解树 / DTW / Shapley', kind: 'comp' },
    ],
  },
  {
    id: 'layer-semantic',
    title: '语义与编译层',
    subtitle: 'semantic/ + compiler/',
    nodes: [
      { id: 'semantic-json', label: '语义单一事实源', path: 'config/semantic.json', note: '字段 / 口径 / 连接规则登记', kind: 'comp' },
      { id: 'catalog', label: '语义目录', path: 'semantic/catalog.py', note: '字段白名单 + 别名词表', kind: 'comp' },
      { id: 'dsl', label: 'QueryDSL 契约', path: 'semantic/dsl_schema.py', note: 'Pydantic extra="forbid"', kind: 'comp' },
      { id: 'compiler', label: '确定性 SQL 编译器', path: 'compiler/sql_compiler.py', note: '窗口 / 补零 / TopN / 同环比', kind: 'comp' },
      { id: 'dialect', label: '方言渲染层', path: 'compiler/dialect', note: '五引擎方言差异下沉', kind: 'comp' },
    ],
  },
  {
    id: 'layer-retrieval',
    title: '取数治理层',
    subtitle: 'core/retrieval/ · 三层同心圆',
    nodes: [
      { id: 'facade', label: '取数门面 L1', path: 'core/retrieval/tools.py', note: 'execute_dsl_query 确定性核', kind: 'comp' },
      { id: 'gateway', label: '裸 SQL 网关守卫', path: 'core/retrieval/guardrails.py', note: '三重防线 GuardrailViolation', kind: 'guard' },
      { id: 'sql_lift', label: '提升闸门 L2', path: 'core/retrieval/sql_lift.py', note: 'sqlglot 转译回 DSL 契约', kind: 'gate' },
      { id: 'exploration', label: '探索层 L3', path: 'core/retrieval/exploration.py', note: '审批门 + 安全视图受治理执行', kind: 'gate' },
      { id: 'profiling', label: '动态 profiling', path: 'core/retrieval/profiling.py', note: '低基数字段枚举值注入', kind: 'comp' },
      { id: 'quality', label: 'DataQA 结果断言', path: 'core/retrieval/quality.py', note: '空结果 / NULL 率 / 负值 / 唯一性', kind: 'guard' },
      { id: 'pii', label: 'PII 脱敏物化', path: 'core/retrieval/export.py', note: '确定性哈希掩码 + Parquet', kind: 'guard' },
    ],
  },
  {
    id: 'layer-exec',
    title: '执行与治理层',
    subtitle: 'exec/ + security/',
    nodes: [
      { id: 'audit_gate', label: '执行前审计门', path: 'exec/audit.py', note: '笛卡尔积 / 只读违规 REJECTED', kind: 'guard' },
      { id: 'guards', label: '资源护栏', path: 'exec/guards.py', note: '超时中断 / 扫描熔断 / LIMIT 上限', kind: 'guard' },
      { id: 'pool', label: '连接池与缓存', path: 'exec/pool.py', note: '只读连接池 + 查询缓存', kind: 'comp' },
      { id: 'rls', label: '权限守卫', path: 'security/guard.py', note: '表 / 列 / 行级 RLS 作用域收窄', kind: 'guard' },
      { id: 'views', label: '会话安全视图', path: 'security/views.py', note: '禁列物理投影 + RLS 固化', kind: 'guard' },
      { id: 'adapter', label: 'EngineAdapter 注册中心', path: 'exec/engine', note: 'DB_BACKEND 五引擎切换', kind: 'comp' },
    ],
  },
  {
    id: 'layer-storage',
    title: '存储层',
    subtitle: '数仓 / 沙箱 / 数据交换',
    nodes: [
      { id: 'duckdb', label: 'DuckDB', path: '默认后端', kind: 'store' },
      { id: 'mysql', label: 'MySQL', path: 'DB_BACKEND=mysql', kind: 'store' },
      { id: 'postgres', label: 'PostgreSQL', path: 'DB_BACKEND=postgres', kind: 'store' },
      { id: 'clickhouse', label: 'ClickHouse', path: 'DB_BACKEND=clickhouse', kind: 'store' },
      { id: 'hive', label: 'Hive', path: 'DB_BACKEND=hive', kind: 'store' },
      { id: 'parquet', label: 'ParquetRef', path: 'workspace/inputs', note: 'sha256 可审计数据交换', kind: 'store' },
      { id: 'sandbox', label: '沙箱代码解释器', path: 'core/sandbox', note: 'AST 守卫 + 限权 runner + Docker 隔离', kind: 'guard' },
    ],
  },
  {
    id: 'layer-support',
    title: '横切支撑',
    subtitle: '认证 / 审计 / 配置 / 模型网关',
    side: true,
    nodes: [
      { id: 'auth', label: '统一认证', path: 'auth/', note: 'JWT + Session + 登录限流', kind: 'guard' },
      { id: 'audit', label: '审计与指标', path: 'audit/', note: '审计快照 + JSON 日志 + /api/metrics', kind: 'comp' },
      { id: 'persistence', label: '状态外置', path: 'persistence/', note: 'SQLite KV 会话 / 槽位 / 限流', kind: 'store' },
      { id: 'providers', label: '模型供应商网关', path: 'providers/', note: '四协议适配 + Key 加密存储', kind: 'llm' },
      { id: 'config', label: '全局配置', path: 'config/', note: 'settings + json 契约', kind: 'comp' },
    ],
  },
  {
    id: 'layer-quality',
    title: '质量保障',
    subtitle: 'mock/ + eval/ + scripts/',
    side: true,
    nodes: [
      { id: 'mock', label: '确定性 Gmall 数仓', path: 'mock/', note: '种子 42 + 会话行为链 + 埋点入仓', kind: 'ext' },
      { id: 'eval', label: 'Golden 评测', path: 'eval/eval_runner.py', note: 'oracle / agent 双模式', kind: 'ext' },
      { id: 'intent_eval', label: '意图路由评测', path: 'eval/intent_eval.py', note: '词表锚点回归', kind: 'ext' },
      { id: 'matrix', label: '五后端实测矩阵', path: 'scripts/backend_matrix.py', note: '连通 / 实用 / 调度三档体检', kind: 'ext' },
    ],
  },
]

export const EDGES: EdgeSpec[] = [
  // 交互层内部与编排入口
  { source: 'workbench', target: 'api' },
  { source: 'api', target: 'clarify', label: '编排入口' },
  { source: 'api', target: 'intent', label: 'web 链路' },
  { source: 'api', target: 'tasks' },
  { source: 'tasks', target: 'sse', dashed: true },
  { source: 'critique', target: 'sse', label: '九类事件', dashed: true },
  { source: 'sse', target: 'workbench' },

  // 编排六节点链
  { source: 'clarify', target: 'plan' },
  { source: 'plan', target: 'query' },
  { source: 'query', target: 'analyze' },
  { source: 'analyze', target: 'critique' },
  { source: 'critique', target: 'synthesize' },
  { source: 'critique', target: 'plan', label: '重规划 ≤3', dashed: true },

  // 智能层与语义编译
  { source: 'intent', target: 'nl2dsl' },
  { source: 'nl2dsl', target: 'dsl' },
  { source: 'memory', target: 'nl2dsl', label: '指代继承', dashed: true },
  { source: 'heal', target: 'nl2dsl', label: '重写回写', dashed: true },
  { source: 'semantic-json', target: 'catalog' },
  { source: 'catalog', target: 'compiler', label: '连接规则' },
  { source: 'dsl', target: 'rls', label: '作用域收窄' },
  { source: 'rls', target: 'compiler' },
  { source: 'compiler', target: 'dialect' },

  // 执行链
  { source: 'dialect', target: 'audit_gate' },
  { source: 'audit_gate', target: 'guards' },
  { source: 'guards', target: 'adapter' },
  { source: 'adapter', target: 'duckdb' },
  { source: 'adapter', target: 'mysql' },
  { source: 'adapter', target: 'postgres' },
  { source: 'adapter', target: 'clickhouse' },
  { source: 'adapter', target: 'hive' },

  // 取数治理（三层同心圆）
  { source: 'plan', target: 'facade', label: '取数步骤' },
  { source: 'toolagent', target: 'facade' },
  { source: 'sql_lift', target: 'compiler', label: '提升后重编译' },
  { source: 'sql_lift', target: 'heal', label: '拒升清单', dashed: true },
  { source: 'gates', target: 'exploration', label: '第四类审批', dashed: true },
  { source: 'exploration', target: 'views' },
  { source: 'views', target: 'guards', label: '受治理执行' },
  { source: 'duckdb', target: 'quality', label: '五引擎同路回流' },
  { source: 'quality', target: 'pii' },
  { source: 'pii', target: 'parquet' },
  { source: 'parquet', target: 'sandbox' },
  { source: 'sandbox', target: 'analyze', label: '分析结果' },
  { source: 'analyze', target: 'skills', label: '归因调度' },
  { source: 'profiling', target: 'plan', label: '枚举值注入', dashed: true },

  // 横切支撑
  { source: 'auth', target: 'api', dashed: true },
  { source: 'persistence', target: 'api', dashed: true },
  { source: 'audit', target: 'api', label: '/api/metrics', dashed: true },
  { source: 'providers', target: 'plan', label: '模型调用', dashed: true },
  { source: 'providers', target: 'heal', dashed: true },
  { source: 'config', target: 'semantic-json', dashed: true },

  // 质量保障
  { source: 'mock', target: 'duckdb', label: '入仓', dashed: true },
  { source: 'eval', target: 'api', label: '端到端评测', dashed: true },
  { source: 'intent_eval', target: 'intent', label: '词表锚点', dashed: true },
]

