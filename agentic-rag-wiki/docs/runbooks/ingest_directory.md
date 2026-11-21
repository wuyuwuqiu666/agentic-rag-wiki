# Runbook：目录批量入库（FR-ING-1 存量遍历 / M4-ce 1b）

范围口径=执行档（单机）。本入口把**本机可读目录**里的存量文档批量送进摄取链（解析 → 切块 → 准入 → 双索引）。
跨进程位点、逐文档真实元数据均不在此档（分别＝放行项②（真实 PG 位点）／㉒（真实源连接器）），回执会如实标注，不冒充已支持。
可读格式与单文件上传**同一端口、同一口径**：`.docx/.pptx/.pdf/.txt/.md` 直接抽，扫描版 PDF 的图像页由本机 OCR 逐页识别
（M4-cf 收口放行项⑦：前置、页态与耗时档见 `docs/runbooks/ingest_scan_ocr.md`；缺 OCR 供给时该份进 `skipped` 带安装指路，不静默入空正文）；
旧版 `.doc/.ppt` 由本机 Word/PowerPoint 转换后走同一抽取器（前置与异常见 `docs/runbooks/ingest_office_legacy.md`；
本机不具备该供给或文件不是真 OLE 容器时进 `skipped` 带原因）；
电子表格 `.xls/.xlsx` 不在本档（缺表格正文抽取档=放行项⑧），同样进 `skipped` 逐条陈列、不静默跳过。
页级账的呈现差异如实说明：本入口按**份**计五本账、不逐份陈列 `ocr_pages`/`failed_pages`（个别页认不出字＝该页不进索引、不阻断整份），
需要看到页号明细就用单文件上传入口（回执带页态三项），两处抽取的正文本身一致。

## 前置（缺任何一条都不许读盘）
1. 配允许根目录：`APP_INGEST_SOURCE_DIR=<目录绝对路径>`（见 `.env.example`）。**缺配即整入口 501**——这是默认态，不是故障。
2. 摄取根目录与产物目录**分开放**：目标目录若包含 `APP_ARTIFACT_DIR`（默认 `workspace/`）会被 422 拒收。
   原因：把系统自己写出的 canonical 草稿/决议记录/对账报告吸回源文档＝自灌回路，既污染检索语料又让"删草稿"变成"删文档"。
3. 服务与 CLI 走同一围栏单点 `src/platform/gateway/dir_ingest.py`，口径一致：
   - HTTP：`POST /api/ingest/dir`，body `{"directory":"相对路径","domain":"fin","doc_type":"policy","acl_tags":["fin"],"actor":"ops","confirm":true}`
   - CLI：`python -m src.platform.gateway.cli ingest --directory 相对路径 --domain fin --doc-type policy --acl fin --confirm`
   - 前端：知识中心 · 入库 → 页面底部「整目录入库」（知识域/类型/可见域沿用上方表单当前选择）
4. `directory` 只接受**相对于允许根目录**的路径；留空＝整根。绝对路径与含 `..` 的逃逸一律 403。

## 读回执（无一静默丢弃）
`ingested`＝本批入库份数；`not_ingested`＝未入库份数（下面五本账之和）。
- `skipped`：解析失败/超过单份字节上限/抽出正文为空，逐条带 `reason`。
- `conflicts`：两个文件归一出同一 doc_id（如 `a b.md` 与 `a-b.md`），**后到者不覆盖先入库的那份**——改名后重跑。
- `unchanged`：指纹相同且索引在册，幂等跳过（重跑不长账）。
- `changed`：文件内容变了，**本批没有入库**（见下节）。
- `reingested`：曾被人工下架而目录里仍在＝按"源为真相"重新入库。
- `delete_scan`："root"＝整根扫描、已执行"文件消失→删除"；"subdir-off"＝只扫子目录、**未发删除**（局部视野比对全局位点账会把兄弟目录的文件误判成已消失）。
- 另有 `change_total`/`change_deleted`/`change_reindexed`/`change_cache_evicted`＝本次变更事件的条数与三路效应计数；
  `change_applied`/`change_blocked`/`change_failed`（M4-cr）＝逐条结果的三计数，恒等式
  `applied＋blocked＋failed＝＝total`（`total`＝**真实条数**，本批没有变更就是 0，不会被报成 1）。

## 「文件内容变了」为什么不吃，怎么吃
执行档的 `upsert`＝按对象存储里的**旧**正文重拉重放；用它表达"文件改了"会把旧内容再入库一次（静默错值）。
正确路径两步：
1. 下架该 doc_id：`POST /api/ingest/change` `{"kind":"delete","doc_id":"...","actor":"...","confirm":true}`（或左栏「下架与恢复」）；完整步骤、回执字段读法与两种不可恢复的区分＝`ingest_retire.md`（唯一源）
2. 重跑同目录：索引在册判据转假 → 该文件经 `reingested` 以新正文入库。
"同 doc_id 版本递增并摘除旧版本块"属 canonical 版本语义＝M1 冻结评审面，本入口不自造。

## 异常处置
| 现象 | 含义 | 动作 |
|---|---|---|
| 501 | 未配允许根目录 | 配 `APP_INGEST_SOURCE_DIR` 后重试；不要改代码放开任意读盘 |
| 403 | 请求目录越出根（含符号链接逃逸） | 把文件放进根内，或（由运维）改根配置并重启；错误理由里有 resolve 后的实际路径 |
| 422（非目录） | 目标不存在或不是目录 | 校对相对路径大小写与层级 |
| 422（含产物目录） | 自灌围栏 | 摄取根与 `APP_ARTIFACT_DIR` 分开放 |
| `skipped` 原因含 `[ocr]` 安装指路 | 该份是扫描版 PDF 而本机未装 OCR 供给 | `pip install 'agentic-rag-wiki[ocr]'` 后重启进程重跑本目录（见 `docs/runbooks/ingest_scan_ocr.md`）；整批不因此中断 |
| 503 + `BreakerTripped B1` | 本批删除占比超当日存量阈值（整根扫描时源目录大面积缩水） | 按 `docs/runbooks/breakers.md` B1 页处置；**勿**用子目录扫描绕开 |
| 500 + `receipt.items`（M4-cr） | 批内某条变更落账失败：**其余条照跑**，逐条账随异常带出（谁 applied、谁 blocked、谁 failed 及原因） | 按 `ingest_retire.md`「逐条账」处置：detail 已点名该条**已落地**的部分，先 `GET /api/ingest/reconcile` 看债、再 `POST /api/ingest/compensate` 补齐、然后**重发那一条删除**；勿整批重放（已生效的条会二次动作） |
| 重启后同目录又整批入库一遍 | 指纹位点＝进程内账（放行项②前如实如此） | 索引侧幂等键兜住（同 doc_id 重放不翻倍）；需要跨进程位点即上真实连接器+PG |

## 回滚
- 误入库整批：逐 doc_id 经「下架与恢复」下架（墓碑账保留期内可回滚），或按 `docs/runbooks/restore.md` 走备份恢复锚点。
- 误删：`POST /api/ingest/restore`（`doc_id` 作用域只滚该文档）。
- 本入口不产"再点一次就同步成最新"的语义；任何后续同步都以回执五本账为准。
