# Citation Pipeline 技术方案

## 1. 目标

将当前项目拆成两条可独立运行的链路：

1. `verification pipeline`
   - 输入已有 `BibTeX` 或候选文献列表
   - 执行 DOI / Crossref 校验
   - 输出已校验文献，并可导入 Zotero
   - 作为保底链路长期可用

2. `full citation pipeline`
   - 输入论文段落 / claim
   - 执行 query rewrite、检索、融合、真实性校验、插入决策
   - 如果上游检索不稳定，可随时降级回 verification pipeline

## 2. 系统分层

### 2.1 verification pipeline

```text
BibTeX / candidate list
  ↓
BibTeX parser
  ↓
Crossref verifier
  ↓
verified references
  ├─ JSON / console report
  └─ Zotero importer
```

### 2.2 full citation pipeline

```text
paragraph / claim
  ↓
claim normalization
  ↓
query builder
  ↓
multi-source retrieval
  ↓
candidate normalization
  ↓
dedup + rerank
  ↓
verification gate
  ↓
insertion decision
  ↓
export to citations / BibTeX / Zotero
```

## 3. 设计原则

- verification 与 retrieval 解耦，保证前者始终能单独运行。
- full pipeline 任何阶段失败时，都允许输出 `retrieved but unverified` 或直接回退到 verification-only 流程。
- 当前阶段先不做复杂自动插入，只输出建议候选和置信度。
- 所有阶段保留结构化日志，便于边生产边校验。

## 4. 模块划分

```text
citation_pipeline/
  common/
    config.py
    models.py
    utils.py
  verification/
    bibtex_parser.py
    crossref_verifier.py
    pipeline.py
  retrieval/
    query_builder.py
    pipeline.py
  exporters/
    zotero_importer.py
  full_pipeline.py
```

## 5. 关键数据结构

### 5.1 VerifiedReference

统一承载 verification 输出：

- 原始标题、作者、年份、期刊、DOI
- Crossref 校验后的标题、作者、年份、期刊、DOI
- `needs_review`
- `match_score`

### 5.2 CandidatePaper

统一承载检索候选：

- 来源
- 标题、作者、年份、摘要、venue、doi、url
- retrieval score
- evidence snippet
- raw payload

## 6. 无 gold set 条件下的上线前策略

当前你没有时间先做离线 gold set，因此采用 `production with structured review` 策略，但必须限制自动化范围。

### 6.1 当前建议

- 允许生产使用 retrieval + verification
- 默认只输出 top-k 候选，不直接无条件自动插入
- 高置信候选可标记为 `recommended`
- 低置信候选强制 `needs_review`

### 6.2 边生产边校验

每次运行记录：

- 输入 claim
- top-k 候选
- 最终被用户采纳的文献
- 被拒绝的文献
- 是否通过 verification
- 人工修订后的最终 citation

这些记录直接沉淀为后续 gold-like 数据。

### 6.3 最小在线指标

至少记录：

- verification pass rate
- manual review rate
- candidate acceptance rate
- top1 acceptance rate
- retrieval empty rate
- average latency per segment

## 7. 降级策略

### 7.1 full pipeline 失败

当检索、融合或插入阶段失败时：

- 不阻塞 verification pipeline
- 可直接对现有候选列表 / BibTeX 执行 verification
- 输出人工确认结果

### 7.2 verification 失败

当 Crossref 无法稳定确认时：

- 标记 `needs_review = true`
- 允许导入 Zotero，但在标题或附加字段中提示人工检查

## 8. 当前阶段开发顺序

### Phase A

- 抽离 `verification pipeline`
- 抽离 Zotero importer
- 保持与现有脚本行为一致

### Phase B

- 增加 retrieval 占位接口
- 定义 full pipeline 编排骨架
- 暂不接真实 OpenScholar

### Phase C

- 接入 Semantic Scholar
- 跑在线人工确认工作流
- 累积真实使用日志

### Phase D

- 基于日志反推评测集
- 再决定是否做严格自动插入
