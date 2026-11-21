# Runbook：扫描版 PDF 入库（FR-ING-8 页态账 / M4-cf 放行项⑦）

范围口径=执行档（单机、零外呼）。扫描版 PDF（无文本层）由**服务端本机 OCR** 逐页识别后入库，
识别落在解析边界（`FileParserPort`→`ScanOcrPort`，见 `docs/adr/ADR-009-parse-returns-parsedbody.md`），
因此单文件上传与整目录批量两条入口**同一口径**：同一份文件从哪个入口进，页态与正文都一致。

## 前置（缺任何一条都如实拒绝，不回退成"没字＝空文档"）
1. 装 OCR 供给：`pip install 'agentic-rag-wiki[ocr]'`（=`rapidocr-onnxruntime` + `pypdfium2`，Apache-2.0 / BSD-3-Clause，
   权重随 wheel 分发＝识别过程不出机、纯 CPU）。**没有配置开关**：能力探测在 `RapidScanOcr.available()`，装了就用、缺装就报。
2. 无需重启以外的额外动作：装配层（`composition.py`）在建应用时注入解析档，装好依赖重启进程即生效。
3. 许可证边界：本项目排除 PyMuPDF（AGPL，在 `harness/deps_governance.py` 拒绝表内）；PDF 渲图统一走 pypdfium2。

## 页态怎么读（回执三项，单文件上传）
`page_state` ∈ `digital`（有文本层，不碰 OCR）/ `scanned`（全篇图像页）/ `mixed`（部分页有文本层）。
`ocr_pages`＝由 OCR 认出字的页号（0 起，前端按 1 起展示）；`failed_pages`＝**认不出字、已排除出正文、未进索引**的页号。
`failed_pages` 非空＝回执标红并给出下一步（重扫或另存为带文本层的 PDF 再入库），**绝不**把"半份文档"冒充"整份入库"。
整目录批量回执只按份计五本账（无逐页明细）：某页认不出字＝该页不进索引、不阻断整份；整份全认不出＝该份进 `skipped` 带原因。

## 实测耗时档（本机，非质量/容量基线）
本机单进程、纯 CPU onnxruntime（本通道不走 GPU）：引擎初始化 ~0.5s，单页（`RENDER_SCALE=2.0`，960×308 纯图像页）识别 ~1.2s；
2 页扫描件走完「上传→识别→切块→准入→双索引」~2.6s，含一次问答检索的往返亦在同一量级。
这些是**合成夹具上的本机单次观测**，只用于说明"同步入口不会分钟级卡住"，
不得当作生产吞吐/准确率基线：真实扫描语料的字号、倾斜、印章与表格密度都未在此标定（金标真机=放行项面）。

## 缺供给时的表现（装了与否都有确定答复）
| 现象 | 含义 | 动作 |
|---|---|---|
| 单文件上传 422，正文含 `pip install 'agentic-rag-wiki[ocr]'` | 整本都是图像页且本机无 OCR 供给 | 装 extra 后重启进程重传；或把该件另存为带文本层的 PDF |
| 单文件上传 200 且 `failed_pages=[...]`、`page_state=mixed` | 混合件：有文本层的页照收，图像页因缺供给认不出 | 回执如实；装供给后重传同一文件可补齐（同 doc_id 重放，索引不翻倍） |
| 整目录 `skipped[].reason` 含 `ScanOcrUnavailable`/OCR 指路 | 该份在批量档里被跳过，其余份不受影响 | 同上；批量档**不因单份解析失败中断整批** |
| 422「PDF N 页经 OCR 后仍全无正文（疑空白或图像质量不足）」 | 有 OCR 供给但一页字都没认出来 | 属真实质量问题：换更清晰的扫描件；不要为此放宽准入门限 |

## 回滚
- 误入库扫描件：走「下架与恢复」（`POST /api/ingest/change` `kind=delete`），保留期内可 `POST /api/ingest/restore` 回滚（步骤与字段读法＝`ingest_retire.md`）。
- 装/卸 OCR 供给不改已入库内容：识别只发生在解析时，正文入 canonical 后与 OCR 无关；
  需要"补认缺失页"＝装好供给后**重传同一文件**（不是改代码放开空文档准入）。
- 本通道无独立开关可关：如需彻底禁用识别，卸载 `[ocr]` extra 即回到"图像页如实记失败页"的拒绝态，系统不会假装识别过。
