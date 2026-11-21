"""扫描版 PDF 的 OCR 旁路契约（M4-cf，放行项⑦ 收口）。

两条主责：①**逐页路由**＝有文本层的页不碰 OCR（成本与不确定性都不该付）、无文本层的页交 `ScanOcrPort`、
认不出字的页＝失败页**排除出正文并记账**（绝不用半份文本冒充整份文档，也绝不伪造文字）；
②**页态如实**＝digital/scanned/mixed 由真·逐页判据给出，且随正文一起进 `Document.page_state`（ADR-009）。

桩件走全部路由（离线确定性，CI 无 OCR 也跑），真引擎另有一条 `skipif` 用例——它是 pypdfium2 渲染参数与
RapidOCR 调用形状的**唯一执法**：桩件测不到那条分支，写错会让所有扫描件静默变 4xx。
另有一条 SDK 落点锁：`rapidocr_onnxruntime`/`pypdfium2`/`numpy` 只允许出现在 `providers/scan_ocr_rapid.py`
（`harness/sdk_blacklist.txt` 属禁改区，故承 M4-ce 1c 的做法在 tests 侧立 AST 锁，§6 禁令1 同族口径）。
"""

from __future__ import annotations

import ast
import io
from pathlib import Path

import pytest
from src.platform.providers.errors import ProviderNotConfigured
from src.platform.providers.fileparser_dev import DevFileParser, FileParseError, ParsedFile
from src.platform.providers.scan_ocr_rapid import RapidScanOcr
from src.platform.types import Document

_ROOT = Path(__file__).resolve().parents[2]
_OCR_SDKS = {"rapidocr_onnxruntime", "pypdfium2", "numpy"}


def _make_pdf(pages: list[str | None]) -> bytes:
    """stdlib 现场拼一份结构合法的多页 PDF：`None`＝该页**无文本层**（图像页，扫描件的样子）。"""
    count = len(pages)
    font_no = 3 + 2 * count
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(count))
    objs: list[bytes] = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        f"<</Type/Pages/Count {count}/Kids[{kids}]>>".encode(),
    ]
    for idx, text in enumerate(pages):
        content_no = 4 + 2 * idx
        if text:
            stream = f"BT /F1 12 Tf 20 150 Td ({text}) Tj ET".encode()
            objs.append(
                f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents {content_no} 0 R"
                f"/Resources<</Font<</F1 {font_no} 0 R>>>>>>".encode()
            )
        else:
            stream = b""
            objs.append(f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents {content_no} 0 R>>".encode())
        objs.append(b"<</Length " + str(len(stream)).encode() + b">>stream\n" + stream + b"\nendstream")
    objs.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for num, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{num} 0 obj".encode() + body + b"endobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer<</Size {len(objs) + 1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF".encode()
    return bytes(out)


class _FakeOcr:
    """ScanOcrPort 桩件：按页码回脚本化文本，并记下每次被请求的页（判"该不该碰 OCR"就靠这份账）。"""

    def __init__(self, *, texts: dict[int, str] | None = None, available: bool = True, error: Exception | None = None) -> None:
        self._texts = texts or {}
        self._available = available
        self._error = error
        self.requested: list[list[int]] = []

    def available(self) -> bool:
        return self._available

    def recognize_pages(self, data: bytes, page_indices: list[int]) -> dict[int, str]:
        self.requested.append(list(page_indices))
        if self._error is not None:
            raise self._error
        return {idx: self._texts.get(idx, "") for idx in page_indices}


def _parser(ocr: object) -> DevFileParser:
    return DevFileParser(scan_ocr=ocr)  # type: ignore[arg-type]


def test_text_layer_document_never_touches_ocr() -> None:
    """有文本层＝主车道：一次 OCR 都不发起（扫描件旁路不是"凡事都先认一遍"）。"""
    ocr = _FakeOcr()
    parsed = _parser(ocr).parse("policy.pdf", _make_pdf(["allowance 500", "second clause"]))
    assert parsed.page_state == "digital" and parsed.ocr_pages == () and parsed.failed_pages == ()
    assert "allowance 500" in parsed.text
    assert ocr.requested == []


def test_image_only_pages_are_ocrd_and_reported_scanned() -> None:
    ocr = _FakeOcr(texts={0: "差旅报销制度", 1: "市内交通费上限八十元"})
    parsed = _parser(ocr).parse("scan.pdf", _make_pdf([None, None]))
    assert parsed.page_state == "scanned"
    assert parsed.ocr_pages == (0, 1) and parsed.failed_pages == ()
    assert ocr.requested == [[0, 1]]  # 只请求缺文本层的那两页，且按页码序
    assert "差旅报销制度" in parsed.text and "市内交通费上限八十元" in parsed.text


def test_mixed_document_keeps_textlayer_pages_and_ocrs_the_rest() -> None:
    """混合件：文本层页原样用、图像页交 OCR，态如实报 mixed（FR-ING-8 的 mixed 分支）。"""
    ocr = _FakeOcr(texts={1: "第二页是扫描图"})
    parsed = _parser(ocr).parse("mix.pdf", _make_pdf(["page one has text", None]))
    assert parsed.page_state == "mixed"
    assert parsed.ocr_pages == (1,) and parsed.failed_pages == ()
    assert ocr.requested == [[1]]  # 第 0 页有字，不重认
    assert "page one has text" in parsed.text and "第二页是扫描图" in parsed.text


def test_unrecognized_page_is_dropped_and_counted_not_fabricated() -> None:
    """失败页＝不进正文、但要进账：回执里 `failed_pages` 看得见，不是"抽出了 N 字符"的半个真相。"""
    ocr = _FakeOcr(texts={0: "第一页认出来了", 1: "   "})
    parsed = _parser(ocr).parse("part.pdf", _make_pdf([None, None]))
    assert parsed.text == "第一页认出来了"
    assert parsed.failed_pages == (1,)
    assert parsed.page_state == "scanned"  # 两页都无文本层：态按容器判，不因识别成功/失败而改


def test_all_pages_unrecognized_fail_loud_never_indexes_empty() -> None:
    ocr = _FakeOcr(texts={})
    with pytest.raises(FileParseError, match="仍全无正文"):
        _parser(ocr).parse("blank.pdf", _make_pdf([None, None]))


def test_missing_ocr_supply_still_reports_state_and_points_to_extra() -> None:
    """供给不在位也不猜：有字的页照旧入库（不回退今天的行为），无字页记进账且页态不许冒充 digital。"""
    ocr = _FakeOcr(available=False)
    parsed = _parser(ocr).parse("half.pdf", _make_pdf(["有文本层的一页", None]))
    assert parsed.page_state == "mixed" and parsed.failed_pages == (1,) and parsed.ocr_pages == ()
    assert ocr.requested == []  # 不可用＝连识别都不发起（与 M4-ce 1c 的 Office 侧同一口径）

    strict = _parser(_FakeOcr(available=False))
    with pytest.raises(FileParseError, match=r"\[ocr\]"):
        strict.parse("scan.pdf", _make_pdf([None]))


def test_engine_failure_maps_to_file_parse_error_not_empty_text() -> None:
    """OCR 侧任何异常（缺依赖/坏容器/页码越界）都必须以 FileParseError 上浮→4xx，绝不退化成"这页没字"。"""
    ocr = _FakeOcr(error=ProviderNotConfigured("PDF 光栅化失败（疑损坏或加密）：boom"))
    with pytest.raises(FileParseError, match="boom"):
        _parser(ocr).parse("scan.pdf", _make_pdf([None]))


def test_parsed_body_satisfies_the_repo_shape() -> None:
    """repo 声明、provider 满足（§6 跨区禁令的反向缝）：`FileSystemSource` 只按 `ParsedBody` 形状消费解析产物。"""
    from src.partitions.ingest.repo.source import ParsedBody

    assert isinstance(ParsedFile(text="x", page_state="scanned"), ParsedBody)
    doc = Document(doc_id="d1", raw_text="x", page_state=ParsedFile(text="x", page_state="scanned").page_state)
    assert doc.page_state == "scanned"  # 页态进一级契约字段→管线第⓪刀的门禁据此生效


def test_ocr_sdks_are_confined_to_one_provider_file() -> None:
    """SDK 落点锁（§6 禁令1 同族）：OCR/光栅化/数值库只能在 `providers/scan_ocr_rapid.py` 出现。

    `harness/sdk_blacklist.txt` 属禁改区且本管第三方 SDK 目录豁免，覆盖不到这条更细的落点要求，
    故与 M4-ce 1c 的进程边界锁一样立在 tests 侧（P2：每个约束=一个可机检产物）。
    """
    allowed = (_ROOT / "src" / "platform" / "providers" / "scan_ocr_rapid.py").resolve()
    offenders: list[str] = []
    for py in sorted((_ROOT / "src").rglob("*.py")):
        if "__pycache__" in py.parts or py.resolve() == allowed:
            continue
        names: set[str] = set()
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module.split(".")[0])
        hit = names & _OCR_SDKS
        if hit:
            offenders.append(f"{py.relative_to(_ROOT)}: {sorted(hit)}")
    assert offenders == []


@pytest.mark.skipif(not RapidScanOcr.available(), reason="需本机 OCR 供给（rapidocr-onnxruntime + pypdfium2）")
def test_real_engine_reads_a_scanned_page_no_ocr_supply_cannot_fabricate() -> None:
    """真引擎往返（本机装有 OCR 档时才跑）：现场造纯图像 PDF → 逐页认出中文。

    这条是 pypdfium2 渲染参数（`scale`/`to_pil`）与 RapidOCR 调用形状（`engine(ndarray)` → 行列表）的
    唯一执法：桩件测不到，写错会让所有扫描件静默变 4xx 而套件里无一例能发现。
    """
    from PIL import Image, ImageDraw, ImageFont

    def page(lines: list[str]) -> Image.Image:
        img = Image.new("RGB", (900, 260), "white")
        draw = ImageDraw.Draw(img)
        font = None
        for candidate in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"):
            try:
                font = ImageFont.truetype(candidate, 40)
                break
            except OSError:
                continue
        for i, text in enumerate(lines):
            draw.text((28, 30 + i * 80), text, font=font, fill="black")
        return img

    first, second = page(["差旅报销制度 2026 版", "机票需提前三天预订"]), page(["市内交通费每日上限八十元"])
    buf = io.BytesIO()
    first.save(buf, format="PDF", resolution=150.0, append_images=[second], save_all=True)
    data = buf.getvalue()
    # 夹具自身必须真是"无文本层"：PIL 只写位图、不写字符串，下面 ocr_pages==(0,1) 就是这条的执法
    assert data[:5] == b"%PDF-"

    parsed = DevFileParser().parse("scanned.pdf", data)
    assert parsed.page_state == "scanned"
    assert parsed.ocr_pages == (0, 1) and parsed.failed_pages == ()
    assert "差旅报销制度" in parsed.text and "提前三天" in parsed.text
    assert "每日上限八十元" in parsed.text
