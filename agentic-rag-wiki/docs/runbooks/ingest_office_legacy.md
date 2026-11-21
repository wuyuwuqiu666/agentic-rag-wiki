# Runbook：旧版 Office 文档入库（`.doc` / `.ppt`，FR-ING-1 / M4-ce 1c）

范围口径=执行档（单机 Windows）。旧版二进制 OLE 容器不由 python 解析：先判**容器真伪**，再交**本机 Word/PowerPoint**
经 PowerShell COM 转成 OOXML，最后走与 `.docx`/`.pptx` 完全相同的抽取器与摄取链（解析 → 切块 → 准入 → 双索引）。
零新依赖、零网络、不改写源文件（以只读方式打开，产物只写进一次性临时目录）。

## 前置（缺任何一条都直接报错，不静默退化）
1. 运行主机是 Windows，且 PATH 里有 `powershell`；对应程序本机装着：`.doc` 需 Word、`.ppt` 需 PowerPoint。
   能力靠探测（`OfficeComConverter.available()`），**没有开关可拨**——不具备即明确拒绝，不会假装解析成功。
2. 三条入口共用同一解析端口，口径一致：
   - 前端：知识中心 · 入库 → 选择文件（`.docx,.pptx,.pdf,.txt,.md,.doc,.ppt`）
   - HTTP：`POST /api/ingest/file`（`content_b64`=整份文件字节）
   - 整目录：`POST /api/ingest/dir` 或 CLI `ingest`（见 `docs/runbooks/ingest_directory.md`，`.doc`/`.ppt` 自动纳入）
3. 文件必须是**真的旧版容器**。改名的 `.docx`、纯文本、RTF、下载截断的半份文件都会在转换前被容器真伪门拦下。
4. Word/PowerPoint 会话里**不要留着模态对话框**（受保护视图、活动安装提示、恢复文档窗）。转换走无人值守，
   弹框等到超时只会失败；先手工打开一次点掉提示再批量入库。

## 读回执
成功回执与 `.docx` 同形：`source_file`＝原始文件名，`extracted_chars`＝转换后抽出的正文字符数（>0，抽出空文本报 422）。
回执里没有"转换"这一档＝因为转换对下游是透明的，正文一视同仁进同一条链。**不宣称版式/图片/表格被保留**——
进索引的只有文本层。

## 异常处置
| 现象 | 含义 | 动作 |
|---|---|---|
| 422 含「缺 OLE 复合文档魔数」 | 不是旧版二进制容器（多为改名的 docx/pptx、纯文本、RTF） | 按真实格式改扩展名重传；不要把它当 `.doc` 塞 |
| 422 含「扇区参数异常」 | 容器头已损坏 | 从源系统重新取一份；损坏件入库只会污染检索语料 |
| 422 含「不足 OLE 头扇区」 | 文件过小/下载不完整 | 补齐字节再传 |
| 422 含「旧版 .doc 转换失败」 | Office 自己拒开：文件被锁、加密、宏警告或格式超旧；串里带 Office 的回执原因 | 手工打开确认能读→另存为 `.docx` 再传 |
| 422 含「需本机 Office 转换」 | 非 Windows，或没装 Word/PowerPoint，或 PATH 无 powershell | 本机另存为 `.docx`/`.pptx`；或部署带外部转换器（LibreOffice/pandoc）的解析档＝换实现（G5），不改端口 |
| 422 含「Office 转换超时（>90s）」 | 弹框等待、文件巨大或 Office 无响应，未产出 OOXML | 见前置 4；大文件先在 Office 里另存为 OOXML 再入库 |
| 415「暂不支持的文件类型：x.xls」（单文件上传）／`skipped[].reason` 带同句（整目录入库） | 电子表格**缺的是表格正文抽取档**，不是转换器（现代 `.xlsx` 同样未支持） | 转成文档类正文再入库，或等表格原子化切块口径就位（放行项⑧）；本档不塞一份会撞口径的临时 dump |
| 503 + `BreakerTripped` | 写侧总闸（B0）或删除占比闸（B1）在拦，与格式无关 | 按 `docs/runbooks/breakers.md` 处置；勿改用脚本绕开围栏 |

## 临时目录与孤儿进程核查
- 一次性工作目录在系统 TEMP 下，前缀 `rag-office-`，每次转换结束即删（带重试，因为 Office 退出放句柄有 1–2 秒滞后）。
- 正常态残留为 0。若进程被强杀留下现场：下次转换开头会自动清扫**超龄**（>600 秒）的同前缀目录，不必人工干预。
- 手工核查（Windows）：
  ```bat
  dir /b /ad "%TEMP%\rag-office-*"
  tasklist /fi "IMAGENAME eq WINWORD.EXE"
  tasklist /fi "IMAGENAME eq POWERPNT.EXE"
  ```
  残留目录可整目录删除（里面只有本次转换的输入副本与中间产物，不含唯一真相——源文件从未被改写）；
  滞留的 Office 进程若确认无人在用，可 `taskkill /im WINWORD.EXE`。

## 回滚
- 误入库：左栏「下架与恢复」按 doc_id 下架（墓碑保留期内可回滚），或 `POST /api/ingest/change` `{"kind":"delete",...,"confirm":true}`（步骤与字段读法＝`ingest_retire.md`）。
- 转换本身无需回滚：源文件只读、产物落在临时目录并已清除；索引侧的回滚口径与其它格式完全相同。
