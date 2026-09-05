# 评审归档（Review Archive）

本目录统一存放项目**历次评审的落盘文件**（PR 评审、代码审计、生产就绪度评审、安全评审等）。任何评审结束后，落盘文件一律输出到本目录，并遵循以下统一规范。

## 目录约定

- 所有评审落盘文件统一存放于 `docs/reviews/`，**禁止散落在项目根目录或其他目录**。
- 每个评审一份 Markdown 文件；若评审还产出结构化数据（如缺陷清单 CSV/JSON），可一并放入本目录或同级子目录。

## 文件命名规范

```
<类型>-<主题关键词>.md
```

- 类型使用小写 kebab-case 英文关键词，例如：
  - `pr-review-*`：Pull Request / 变更全量评审
  - `audit-*`：深度审计（架构 / 性能 / 安全等）
  - `readiness-*`：生产就绪度评审
- 示例：`pr-review-multi-tool-agent.md`、`audit-code-depth.md`、`readiness-production.md`

## 标题统一格式

文件内首行标题必须遵循以下格式（内容结构不强制统一）：

```
# 评审报告｜<评审类型>：<评审主题>（YYYY-MM）
```

- `<评审类型>`：PR 评审 / 深度审计 / 就绪度评审 / 安全评审 等；
- `<评审主题>`：一句话点明评审对象；
- `YYYY-MM`：评审完成日期（与文件内"评审日期"保持一致）。

示例：

```markdown
# 评审报告｜PR 评审：多工具编排（Multi-Tool Agent）升级（2025-06）

# 评审报告｜深度审计：代码深度与生产健壮性（2026-09）

# 评审报告｜就绪度评审：生产就绪度（Production Readiness）（2026-09）
```

## 推荐内容骨架（可选，不强制）

建议在标题后附带评审元信息，便于检索：

```markdown
> 评审日期：YYYY-MM-DD · 评审方式：... · 评审范围：...
```

## 归档清单

| 文件 | 评审报告 | 日期 |
| --- | --- | --- |
| [pr-review-multi-tool-agent.md](pr-review-multi-tool-agent.md) | PR 评审：多工具编排升级 | 2025-06 |
| [code-depth-audit.md](code-depth-audit.md) | 深度审计：代码深度与生产健壮性 | 2026-09 |
| [production-readiness-audit.md](production-readiness-audit.md) | 就绪度评审：生产就绪度 | 2026-09 |
| [audit-chatbi-domain-specificity.md](audit-chatbi-domain-specificity.md) | 深度审计：ChatBI 领域专业性与问数能力穿透 | 2026-09 |
| [repair-chatbi-domain-specificity.md](repair-chatbi-domain-specificity.md) | 缺陷修复：ChatBI 领域专业性整改闭环 | 2026-09 |