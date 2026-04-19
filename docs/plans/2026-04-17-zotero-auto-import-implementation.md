# Zotero DOI Auto Import Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现“提取 DOI 后自动导入 Zotero（每次新建 Collection）”，并在轻量化前端可视化展示全流程进度。

**Architecture:** 在现有 `streamlit_app.py` 中保持页面入口不变，新增一个工作流层负责 `citation -> DOI -> Zotero` 逐条处理。扩展 `ZoteroImporter` 支持创建任务级 Collection、按 DOI 导入及去重检查，前端通过状态字段展示流程进度与结果。

**Tech Stack:** Python 3、Streamlit、pyzotero、habanero、python-dotenv、unittest

---

### Task 1: 增加 workflow 测试（先失败）

**Files:**
- Create: `tests/test_doi_zotero_workflow.py`
- Test: `tests/test_doi_zotero_workflow.py`

1. 新增测试覆盖：
   - 有 DOI 且允许导入时，应返回 `zotero_imported`
   - DOI 缺失时，应返回 `doi_not_found`
   - Zotero 已有 DOI 且开启跳过，应返回 `zotero_skipped_duplicate`
2. 运行测试并确认初次失败（模块/行为尚未实现）。

### Task 2: 实现 DOI->Zotero workflow

**Files:**
- Create: `citation_pipeline/doi_zotero_workflow.py`

1. 定义 `process_citations(...)`，串联 parse、Crossref lookup、Zotero 导入。
2. 输出统一行结果（序号、DOI、阶段、状态、错误、Zotero key 等）。
3. 聚合 summary（总数、找到 DOI、导入成功、重复跳过、失败）。

### Task 3: 扩展 ZoteroImporter 导入能力

**Files:**
- Modify: `citation_pipeline/exporters/zotero_importer.py`

1. 新增创建 Collection 方法（支持每次新建任务集合）。
2. 新增 DOI 规范化与重复检查（按 DOI 查询现有条目）。
3. 新增按 DOI 查询结果构建并导入 item 的方法，返回 item key。

### Task 4: 集成 Streamlit 前端

**Files:**
- Modify: `streamlit_app.py`

1. 增加“自动导入 Zotero”“跳过重复 DOI”“Collection 前缀”配置。
2. 接入 workflow，逐条更新进度文本与进度条。
3. 展示全流程统计卡片与明细表（含导入状态）。

### Task 5: 配置与验证

**Files:**
- Modify: `.env.example`

1. 增加 Zotero 自动导入相关环境变量示例。
2. 运行 `python -m unittest discover -s tests -v` 验证测试通过。
3. 运行 lints 检查改动文件是否引入新问题。
