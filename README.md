# Zotero / Citation Pipeline

> 这个项目就是为了解决一个很现实的问题：毕业论文时间紧，引用处理太慢。  
> 先把最费时间的步骤提速，再谈更高级的自动化。

## 为什么做这个项目

这个项目不是从产品化目标开始，而是我写毕业论文时遇到的实际问题：引用处理很耗时间。平时找文献，我主要走两条路径。第一条是先写好一段内容，再让 AI 按语义或 query 找候选文献。第二条是看师兄论文或综述里的参考文献，觉得合适就直接使用。问题是这两条路最后都卡在同一个地方：核验和导入太慢。尤其第二条，一条条复制粘贴到 Zotero，时间成本很高。

我做这个项目的核心目标只有一个：省时间。重点不是追求复杂能力，而是缩短从 claim 到可用引用的路径。我的做法也很直接：能自动化的重复操作就自动化（比如批量校验、批量导入），必须人工判断的地方就保留，避免最后集中返工。针对第二条路径，我最想解决的是批量自动导入 Zotero，减少机械的复制粘贴。

所以这套实现更偏务实：`verification` 负责 `BibTeX -> 校验/纠偏 -> Zotero 导入`，`full` 负责 `claim/query -> 候选检索 -> verification -> ranking`，再配合日志做复核记录。协作者拿过去就能直接用，不用先理解复杂模型，也不用承担全自动误判的风险。

如果你和我一样，现在最重要的是把论文往前推进，这个项目的价值就看三件事：减少重复操作、减少工具切换、减少后期因为引用问题返工。当前我也给它定了边界：先不追求自动往正文里插引文，先把稳定提效这件事做扎实，再逐步扩展。

当前项目现在支持两种运行模式：

- `verification`：校验已有 `BibTeX` 并导入 Zotero
- `full`：输入单条或批量 claim，执行 query 构建、Semantic Scholar 检索、Crossref 校验、ranking，并记录 review log / feedback log


## 1. 环境准备

建议使用 Python 3.10+

安装依赖：

```bash
pip install -r requirements.txt
```

## 2. 配置

复制 `.env.example` 为 `.env`，填写配置：

```bash
copy .env.example .env
```

## 3. 你必须手动填写的信息

### 3.1 只跑 verification 模式时，必须填写

- `ZOTERO_LIBRARY_ID`
- `ZOTERO_LIBRARY_TYPE`：`user` 或 `group`
- `ZOTERO_API_KEY`
- `CROSSREF_MAILTO`

### 3.2 跑 full 模式时，必须填写

- `ZOTERO_LIBRARY_ID`
- `ZOTERO_LIBRARY_TYPE`
- `ZOTERO_API_KEY`
- `CROSSREF_MAILTO`
- `PIPELINE_MODE=full`
- 二选一：
  - `CLAIM_TEXT=你要检索 citation 的正文/claim`
  - `CLAIMS_INPUT_PATH=claims.txt` 或 `.jsonl` 文件路径

### 3.3 strong recommended，但当前不是绝对必填

- `SEMANTIC_SCHOLAR_API_KEY`
- `SEGMENT_ID`
- `SEMANTIC_SCHOLAR_TOP_K`
- `REVIEW_LOG_PATH`
- `REVIEW_FEEDBACK_PATH`

## 4. verification 模式

默认模式为 `verification`：

```bash
python main.py
```

流程：

1. 读取 `reference.bib`
2. 用 Crossref 校验 / 纠偏
3. 导入 Zotero

## 5. full 模式：单条 claim

```bash
PIPELINE_MODE=full
CLAIM_TEXT=Recent machine learning methods improve polymer property prediction under low-data settings.
SEGMENT_ID=test_claim_001
```

然后运行：

```bash
python main.py
```

## 6. full 模式：批量 claims

你可以准备一个 `claims.txt`：

```text
Recent machine learning methods improve polymer property prediction under low-data settings.
Graph neural networks have shown promise in materials discovery tasks.
```

然后在 `.env` 中设置：

```bash
PIPELINE_MODE=full
CLAIMS_INPUT_PATH=claims.txt
```

也支持 `.jsonl`，每行例如：

```json
{"segment_id":"intro_001","claim_text":"Recent machine learning methods improve polymer property prediction under low-data settings."}
```

## 7. ranking 现在怎么做

当前是规则打分，不是 LLM rerank。

综合信号包括：

- verification 是否通过
- Crossref 匹配分数
- 标题关键词命中
- citation count
- 年份新近性

输出标签包括：

- `recommended`
- `consider`
- `needs_review`

## 8. feedback log 怎么用

如果你人工看完结果，想顺手记录“最终选了哪篇”，可以在运行前额外填写：

- `SELECTED_RANK`
- `SELECTED_DOI`
- `SELECTED_TITLE`
- `REVIEW_ACTION`
- `REVIEW_NOTES`

程序会把它写到：

```text
review_logs/full_pipeline_feedback.jsonl
```

这一步就是你后续在线评测集的雏形。

## 9. 在线指标统计怎么跑

项目里新增了：

```text
scripts/summarize_review_metrics.py
```

运行：

```bash
python scripts/summarize_review_metrics.py
```

它会读取：

- `review_logs/full_pipeline_reviews.jsonl`
- `review_logs/full_pipeline_feedback.jsonl`

并输出：

- `reviewed_segments`
- `feedback_segments`
- `empty_result_rate`
- `avg_candidates_per_segment`
- `verification_pass_rate`
- `top1_verified_rate`
- `top1_recommended_rate`
- `candidate_acceptance_rate`
- `top1_acceptance_rate`
- `manual_override_rate`
- `recommended_acceptance_rate`
- `consider_acceptance_rate`
- `recommendation_distribution`
- `accepted_label_distribution`
- `feedback_action_distribution`
- `selected_rank_distribution`

### 新增指标含义

- `top1_verified_rate`：每个 segment 的 top1 候选中，有多少比例是通过 verification 的。
- `top1_recommended_rate`：每个 segment 的 top1 候选中，有多少比例被系统打成 `recommended`。
- `manual_override_rate`：有反馈的 segment 中，人工最终选择的不是 rank 1，而是更后面的候选的比例。
- `recommended_acceptance_rate`：所有被系统标记为 `recommended` 的候选中，最终被人工选中的比例。
- `consider_acceptance_rate`：所有被系统标记为 `consider` 的候选中，最终被人工选中的比例。

这些指标能帮你判断：

- top1 是不是已经足够可靠
- ranking 是否真的有用
- 人工是否经常推翻系统首选
- `recommended` 标签是不是值得信任

## 10. 当前文件召回方式到底是什么

**现在不是 LLM 先深度语义理解再拆关键词。**

当前第一版是一个 **rule-based query building + Semantic Scholar search** 流程：

1. 对 claim 做基础标准化
2. 用正则抽取英文 token
3. 去掉 stopwords
4. 生成 1-2 个简化 rewrite query
5. 把原 claim 和 rewrite 一起送给 Semantic Scholar 的 paper search API
6. 合并去重结果
7. 再用 Crossref 做真实性校验
8. 最后做 ranking

也就是说，现在更接近：

- **轻量规则语义压缩**
- 不是 **LLM semantic parsing**
- 不是 embedding rerank
- 也不是 agentic decomposition

## 11. 当前限制

- full 模式当前只接了 `Semantic Scholar`
- 还没有接 `OpenScholar`
- 还没有自动插 citation，只输出候选和 review / feedback logs
- 线上评估依赖这些日志逐步积累
