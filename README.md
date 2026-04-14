# Zotero 自动纠偏与批量导入

基于 `prd.md` 实现的单机版 Python 工具：

- 读取 `reference.bib`
- 用 Crossref 校验/纠偏 DOI
- 自动创建 Zotero Collection
- 对无法可靠匹配的条目以 `⚠️ [需手动检查]` 形式降级导入

## 1. 环境准备

建议使用 Python 3.10+

安装依赖：

```bash
pip install -r requirements.txt
```

## 2. 配置

复制 `.env.example` 为 `.env`，填写你的真实配置：

```bash
copy .env.example .env
```

需要填写：

- `ZOTERO_LIBRARY_ID`
- `ZOTERO_LIBRARY_TYPE`：个人库填 `user`，群组库填 `group`
- `ZOTERO_API_KEY`
- `CROSSREF_MAILTO`
- 可选：`CROSSREF_SCORE_THRESHOLD`
- 可选：`INPUT_BIB`
- 可选：`COLLECTION_PREFIX`
- 可选：`REQUEST_DELAY_SECONDS`

## 3. 输入文件

默认读取当前目录下的 `reference.bib`。

请确保 `.bib` 条目顺序就是你希望在本次导入中保留的顺序。

## 4. 运行

```bash
python main.py
```

## 5. 导入逻辑

1. 解析 `reference.bib`
2. 优先校验原始 DOI
3. DOI 失效时，用标题 + 第一作者 + 年份做 Crossref 模糊搜索
4. 命中分数达到阈值时接受结果
5. 未命中时仍导入 Zotero，但会在标题前加 `⚠️ [需手动检查]`

## 6. 使用后检查

导入结束后请在 Zotero 中：

1. 打开本次新建的 Collection
2. 搜索 `⚠️`
3. 逐条手动补正异常文献的元数据

## 7. 项目文件

- `main.py`：主程序
- `.env.example`：配置模板
- `reference.bib`：输入 BibTeX 文件示例
- `prd.md`：产品需求文档
- `requirements.txt`：依赖列表

## 8. 注意事项

- Zotero API Key 必须同时具备读取和写入权限。
- Crossref 查询依赖网络连接。
- 这是单机版脚本，不处理多设备同步排序问题。
