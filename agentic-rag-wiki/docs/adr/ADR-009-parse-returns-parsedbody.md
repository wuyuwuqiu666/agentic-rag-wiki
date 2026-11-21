# ADR-009：解析产物改为 ParsedBody（正文 + 页态 + OCR 账），扫描页识别落在解析边界

- 状态：采纳（2026-10-12，用户批复「入库区全部放行」「RapidOCR（onnxruntime）优先」）
- 关联：FR-ING-8（三态探测 digital/scanned/mixed）、FR-ING-1（文件摄取）、放行项⑦（真 OCR，本刀闭合）、§5 providers（`FileParserPort`/`OcrPort`）、§6 跨区三禁（repo 不 import providers）、`providers/fileparser_dev.py`、`providers/scan_ocr_rapid.py`、`partitions/ingest/repo/source.py`、`gateway/http.py`。

## 背景
放行项⑦ 落地前，`.pdf` 抽取只有文本层道：无文本层＝`FileParseError`→422，扫描件根本进不来。补 OCR 时有两个落点可选：

1. **落在准入侧**（既有 `OcrPort.recognize(document)`，管线第⓪刀调）：看起来改动最小，但 `Document` 只带 `raw_text`，**字节已经丢了**——要识别就得把原始字节再传到管线里（新增一条"原始文件"通道＝一级契约变更），或者让解析器把 PDF 字节塞进某个字段（脏）。
2. **落在解析边界**（`FileParserPort.parse(filename, data)` 内）：这里本来就握着页字节，逐页判"有没有文本层"是它的天然职责。

另有一条既有缺陷与本刀同源：`page_state` 此前由**入口事后猜**（上传端点一律默认 `digital`，粘贴文本也是 `digital`）。三态判据只有摸过字节的人能给；让门面猜＝同一份文档换个入口给不同页态，正是本项目反复拦的**口径分叉族**。

## 决策
1. **`FileParserPort.parse` 的返回形状由 `str` 改为 `ParsedBody`**：`text` + `page_state` + `ocr_pages` + `failed_pages`（页码 0 基，元组）。四个字段都由解析边界逐页判出，随正文一起出。
2. **扫描页识别（供给侧）落在解析边界**：新增本地 `ScanOcrPort`（`available()` / `recognize_pages(data, page_indices) -> dict[int, str]`），实现＝`providers/scan_ocr_rapid.py`（RapidOCR onnxruntime + pypdfium2 光栅化，纯 CPU、零外呼）。逐页路由：有文本层的页**不碰 OCR**；无文本层的页交 OCR；认不出字的页＝**失败页**，排除出正文但进账。
3. **`OcrPort` 语义收窄为准入侧门禁**：`PassthroughOcr` 把解析边界已识别的正文交质量门，**不再自称识别**；同一页不认第二遍。企业档在此缝换"读侧再取外部 OCR 结论"，接口不变（G5）。
4. **`repo` 侧声明所需形状，不 import providers**：`partitions/ingest/repo/source.py` 本地声明 `ParsedBody` Protocol（`text`/`page_state`），provider 满足它——依赖方向仍是 repo←provider 之外的注入（§6 跨区三禁不破）。
5. **页态贯通**：`Document.page_state` 由解析产物回填（单文件上传端点与整目录批量入口同一口径），回执加 `page_state`/`ocr_pages`/`failed_pages` 三项。

## 理由
- **单一事实源**：页态与"哪几页是认出来的"是**同一份字节判断**的产物，分两处判必然分叉（两处、两套阈值、两种答案）。
- **成本与不确定性**：OCR 是有噪声、按页耗时的识别；有文本层的页去 OCR＝白付成本还可能认出错字，故逐页路由不是优化而是正确性要求。
- **失败页不许被"成功"吸收**：抽出 3 页中的 2 页≠整份文档入库。旧口径下要么整份 422、要么静默少内容；`failed_pages` 让回执与审计都看得见缺了哪页，缺的页不进索引（不污染检索命中）。
- **端口形状变更而不是一级契约变更**：`ParsedBody` 只活在 providers↔gateway/repo 的注入缝上，不属 `CanonicalEvent`/`Chunk`/`Document` 那批 F1/E3 冻结件（`contract_compat` 快照的是 pydantic 模型，Protocol 不在其内）；改它不需 R7 一级契约评审，但**必须留 ADR**（本条）。

## 代价与对冲
- 解析器多三个产物字段（消费侧只取需要的）＝面积小；`contract_compat` 基线不变（无 pydantic 模型改动）。
- OCR 是 CPU 活：本机实测引擎初始化 0.52s、单页识别 1.23s（纯图像页 960×308）。批量扫描件的夜窗批调度与逐页位点账属真实队列/PG 放行项，不在本刀谎称达标。
- 供给缺位的机器（无 `[ocr]` extra）：行为**不回退成"当文本层收"**——有字页照收、无字页记 `failed_pages`、页态如实 scanned/mixed，全篇无字才 422 并指路 `pip install 'agentic-rag-wiki[ocr]'`。

## 可逆性
`parse()` 返回形状回退＝改回 `str` 并丢页态，会同时丢掉 FR-ING-8 的判据真实性；如需回滚只回滚 `scan_ocr_rapid.py` 的装配（`DevFileParser(scan_ocr=None)` 注入缺席档），端口形状不动。

## 落地边界（离线 vs 放行）
- 已落地（离线代码路径 + 契约测试 + 本机真引擎往返）：逐页文本层探测、OCR 旁路、失败页账、页态贯通（单文件 HTTP 与整目录同一口径）、SDK 落点单文件锁（`tests/contract/test_scan_ocr.py`，`harness/sdk_blacklist.txt` 属禁改区故在 tests 侧立 AST 锁）。
- 放行项/残余：版面分析级扫描件（倾斜、印章遮挡、表格图）质量属真机评测（金标 FR-MEM-5）；扫描件批处理的夜窗调度与逐页断点=真实队列（放行项⑯ 企业档侧）；外部云 OCR/PaddleOCR 换档＝G5 换实现，不新增旋钮（`available()` 探测）。
