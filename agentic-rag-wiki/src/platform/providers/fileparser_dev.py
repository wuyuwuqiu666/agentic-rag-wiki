"""FileParserPort dev 档：把上传文件的字节抽成**正文＋三态页态**（摄取链**上游**，产物再进 Document）。

设计边界（诚实标注，勿误读为"所有 Office/PDF 全能吃"）：
- `.docx` / `.pptx`＝OOXML zip，用 **stdlib `zipfile` + `xml.etree`** 抽正文（零第三方依赖）；
- `.txt` / `.md`＝utf-8-sig 解码（BOM 容忍、非文本字节 fail-loud）；
- `.pdf`＝逐页判文本层密度（FR-ING-8 三态探测的**真**逐页版，M4-cf）：有文本层的页直接用，
  无文本层的页交 `ScanOcrPort`（执行档＝本机 RapidOCR＋pypdfium2，零外呼；放行项⑦ 已收口）；
  识别后仍无字的页＝**失败页，不进正文**并记账（绝不用半份文本冒充整份文档）；
- `.doc` / `.ppt`＝legacy 二进制 OLE，**先经本机 Office（PowerShell COM）另存为 OOXML**，再由上面的 stdlib 抽取器消费
  （M4-ce 1c，零新 pip 依赖、零外呼；COM 不在位即 `FileParseError` 指路外部转换器＝放行项㉒）；
- **不支持**＝`.xls`（dev 档连现代 `.xlsx` 都无表格正文抽取器，缺的是抽取档不是转换器）与其余类型→`FileParseError`。

本模块只做"字节→正文＋页态"的抽取与格式路由，**不做**切块/准入/索引（那些仍在 `IngestPipeline` 内，B0/B4/DLP/F5 不旁路）。
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from typing import Protocol

from src.platform.providers.errors import ProviderNotConfigured
from src.platform.providers.office_com import OLE_MAP, OfficeComConverter, OfficeConvertError
from src.platform.providers.scan_ocr_rapid import RapidScanOcr
from src.platform.types.document import PageState

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"  # docx 正文命名空间
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"  # pptx DrawingML 命名空间

# 可解析扩展名（小写、去点）；txt 家族同一口径直接解码。
_TEXT_EXT = {"txt", "md", "markdown", "csv", "log"}
_OOXML_TEXT_EXT = {"docx", "pptx"}
_PDF_EXT = {"pdf"}
_OLE_EXT = set(OLE_MAP)  # {"doc","ppt"}：经本机 Office 转 OOXML 后走上列抽取器
# 明确拒绝并给出原因的类型（避免"静默返回空文本"被误当成功入库）。
_UNSUPPORTED_HINT = {
    "xls": "旧版二进制 Excel（.xls）dev 档不支持——缺的是电子表格正文抽取档（现代 .xlsx 同样未支持），"
    "且表格抽取须对齐 FR-ING-7 表格原子化口径（放行项⑧），不在本刀顺手造一份会撞口径的 dump",
    "xlsx": "电子表格（.xlsx）dev 档不支持——同上：需先落表格正文抽取档并对齐表格原子化口径（放行项⑧）",
}
_NO_COM_HINT = (
    "本机 Office 转换不可用（需 Windows + Word/PowerPoint + PowerShell）："
    "请在装有 Office 的机器上入库，或先另存为 .docx/.pptx 再上传（外部转换器＝放行项㉒）"
)
_NO_OCR_HINT = (
    "扫描版 PDF 需 OCR 供给（本机档：pip install 'agentic-rag-wiki[ocr]'＝RapidOCR+pypdfium2，权重随包分发、不出机；"
    "企业档换云 OCR/PaddleOCR 只换 ScanOcrPort 实现）"
)


class OfficeConverterPort(Protocol):
    """OLE→OOXML 转换供给的注入位（G5：企业档可换 LibreOffice/pandoc 等命令行转换器，本模块不感知）。"""

    def available(self) -> bool: ...

    def convert(self, data: bytes, ext: str) -> tuple[str, bytes]: ...


class ScanOcrPort(Protocol):
    """扫描页正文供给的注入位（FR-ING-8 旁路的真实现；执行档＝`RapidScanOcr`，G5 可换云 OCR/PaddleOCR）。

    只按**页码**取字：调用侧已判过"哪些页没有文本层"，这里不承担三态判定（判定口径单点留在本模块）。
    """

    def available(self) -> bool: ...

    def recognize_pages(self, data: bytes, page_indices: list[int]) -> dict[int, str]: ...


@dataclass(frozen=True)
class ParsedFile:
    """`FileParserPort.parse` 的产物（满足 `providers.ports.ParsedBody` 形状）。

    页码一律 **0 基**（与 pypdf/pdfium 一致）；`ocr_pages`/`failed_pages` 随正文一起交出＝回执可核，
    不出现"抽出了 N 字符"把失败页藏起来的半个真相。
    """

    text: str
    page_state: PageState = "digital"
    ocr_pages: tuple[int, ...] = ()
    failed_pages: tuple[int, ...] = ()


class FileParseError(ValueError):
    """文件不可解析/格式不支持——由门面转成 4xx（客户端错误），绝不吞成空文本入库。"""


def _extension(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return base.rsplit(".", 1)[-1].strip().lower() if "." in base else ""


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = zf.read("word/document.xml")
    except KeyError as exc:  # 合法 zip 但缺正文部件
        raise FileParseError(f"docx 缺 word/document.xml（非有效 Word 文档）：{exc}") from exc
    except zipfile.BadZipFile as exc:
        raise FileParseError("docx 不是有效 OOXML（zip）容器") from exc
    root = ET.fromstring(xml)
    paras: list[str] = []
    for p in root.iter(_W + "p"):
        buf: list[str] = []
        for node in p.iter():
            if node.tag == _W + "t" and node.text:
                buf.append(node.text)
            elif node.tag == _W + "tab":
                buf.append("\t")
            elif node.tag == _W + "br":
                buf.append("\n")
        paras.append("".join(buf))
    return "\n\n".join(paras).strip()


def _pptx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            slides = sorted(
                (n for n in zf.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")),
                key=lambda n: int("".join(ch for ch in n.rsplit("/", 1)[-1] if ch.isdigit()) or 0),
            )
            if not slides:
                raise FileParseError("pptx 无幻灯片部件（非有效 PPT）")
            chunks: list[str] = []
            for idx, name in enumerate(slides, 1):
                root = ET.fromstring(zf.read(name))
                paras: list[str] = []
                for p in root.iter(_A + "p"):
                    txt = "".join(n.text or "" for n in p.iter(_A + "t"))
                    if txt.strip():
                        paras.append(txt)
                chunks.append(f"## 幻灯片 {idx}\n" + "\n".join(paras))
    except zipfile.BadZipFile as exc:
        raise FileParseError("pptx 不是有效 OOXML（zip）容器") from exc
    return "\n\n".join(chunks).strip()


def _pdf_pages(data: bytes) -> list[str]:
    """逐页文本层（pypdf，MIT，可选 extra `documents`，仅本文件 import）；缺装/坏件一律 fail-loud。"""
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as exc:  # pragma: no cover — 取决于环境
        raise FileParseError("PDF 解析需可选依赖 pypdf（pip install 'agentic-rag-wiki[documents]'）") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        return [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # pypdf 对损坏/加密 PDF 抛多种异常，统一转 FileParseError 走 4xx
        raise FileParseError(f"PDF 无法读取（损坏或加密）：{exc}") from exc


def _pdf_extract(data: bytes, ocr: ScanOcrPort) -> ParsedFile:
    """FR-ING-8 三态探测的逐页执行：文本层页原样用，无文本层的页交 OCR，认不出的页排除并记账。

    三条不自欺的口径：①OCR 供给不在位时**不猜**——有文本层的页照旧入库（今天的行为不回退），但页态如实报
    scanned/mixed 且把无字页记进 `failed_pages`（不冒充 digital）；②全篇无字且 OCR 认不出＝直接报错，
    绝不入库空文档；③OCR 侧任何异常（缺依赖/坏容器/页码越界）转 `FileParseError`→4xx，不退化成"这页没字"。
    """
    pages = _pdf_pages(data)
    if not pages:
        raise FileParseError("PDF 无页面（空容器）")
    lacking = [i for i, t in enumerate(pages) if not t.strip()]
    ocr_pages: list[int] = []
    failed: list[int] = []
    if lacking and ocr.available():
        try:
            recognized = ocr.recognize_pages(data, lacking)
        except (ProviderNotConfigured, OSError, ValueError) as exc:
            raise FileParseError(f"扫描页 OCR 失败：{exc}") from exc
        for idx in lacking:
            txt = (recognized.get(idx) or "").strip()
            if txt:
                pages[idx] = txt
                ocr_pages.append(idx)
            else:
                failed.append(idx)
    elif lacking:
        failed = list(lacking)  # 无供给：这些页就是"没认出来的页"，账要留、态要报，但不静默改判 digital
    merged = "\n\n".join(t.strip() for t in pages if t.strip())
    if not merged:
        if not ocr.available():
            raise FileParseError(
                f"PDF 无文本层（{len(pages)} 页全为图像）且本机 OCR 供给不在位→ 失败页不进索引：{_NO_OCR_HINT}"
            )
        raise FileParseError(f"PDF {len(pages)} 页经 OCR 后仍全无正文（疑空白或图像质量不足）→ 失败页不进索引")
    if not lacking:
        state: PageState = "digital"
    elif len(lacking) == len(pages):
        state = "scanned"
    else:
        state = "mixed"
    return ParsedFile(text=merged, page_state=state, ocr_pages=tuple(ocr_pages), failed_pages=tuple(failed))


class DevFileParser:
    """`FileParserPort` dev 档实现：按扩展名路由到对应抽取器，产出正文＋页态供摄取链消费。

    `office`＝OLE→OOXML 转换供给注入位、`scan_ocr`＝扫描页 OCR 供给注入位；两者缺省用本机真档
    （`OfficeComConverter`／`RapidScanOcr`），测试注桩件即可脱离 Office/OCR 引擎验路由。
    """

    def __init__(
        self,
        office: OfficeConverterPort | None = None,
        scan_ocr: ScanOcrPort | None = None,
    ) -> None:
        self._office: OfficeConverterPort = office if office is not None else OfficeComConverter()
        self._ocr: ScanOcrPort = scan_ocr if scan_ocr is not None else RapidScanOcr()

    def supported(self, filename: str) -> bool:
        return _extension(filename) in (_TEXT_EXT | _OOXML_TEXT_EXT | _PDF_EXT | _OLE_EXT)

    def _ole_to_ooxml_text(self, data: bytes, ext: str) -> str:
        """旧版 OLE 先转 OOXML 再走**同一支抽取器**（不另起第二套正文解析口径）。"""
        if not self._office.available():
            raise FileParseError(f"{ext} 需本机 Office 转换：{_NO_COM_HINT}")
        try:
            target_ext, converted = self._office.convert(data, ext)
        except OfficeConvertError as exc:
            raise FileParseError(f"旧版 .{ext} 转换失败：{exc}") from exc
        if target_ext == "docx":
            return _docx_text(converted)
        if target_ext == "pptx":
            return _pptx_text(converted)
        raise FileParseError(f"转换器产出了未知格式 .{target_ext}")

    def parse(self, filename: str, data: bytes) -> ParsedFile:
        ext = _extension(filename)
        if ext in _TEXT_EXT:
            try:
                return ParsedFile(text=data.decode("utf-8-sig").strip())
            except UnicodeDecodeError as exc:
                raise FileParseError(f"{filename} 疑非文本（{ext}）：utf-8 解码失败") from exc
        if ext == "docx":
            return ParsedFile(text=_docx_text(data))
        if ext == "pptx":
            return ParsedFile(text=_pptx_text(data))
        if ext in _OLE_EXT:
            return ParsedFile(text=self._ole_to_ooxml_text(data, ext))
        if ext in _PDF_EXT:
            return _pdf_extract(data, self._ocr)
        hint = _UNSUPPORTED_HINT.get(ext, f"暂不支持的文件类型 .{ext}（dev 档仅 docx/pptx/doc/ppt/pdf/txt/md）")
        raise FileParseError(hint)
