# Zotero / Citation Pipeline

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
