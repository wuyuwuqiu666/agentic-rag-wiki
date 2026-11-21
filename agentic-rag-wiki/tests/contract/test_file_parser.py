"""FileParserPort dev 档契约（M4-bz，M4-ce 1c 扩到 legacy OLE）：文件字节→纯文本抽取 + 不支持格式 fail-loud。

口径：docx/pptx 走 stdlib OOXML 解析（zipfile+xml）、pdf 走 pypdf 文本层、txt/md 走 utf-8 解码；
`.doc`/`.ppt`＝先过**容器真伪门**再交本机 Office（PowerShell COM）转成 OOXML，然后走**同一支抽取器**；
夹具全部用 stdlib 现场生成（不依赖外部 Office/PDF 二进制、不引 python-docx/pptx），COM 经注入桩件演练。
断言"能抽出的抽出、抽不出的明确报错"——绝不把损坏/扫描/不支持静默退化为空文本（那会被误当成功入库）。
真实 COM 往返另有一条 `skipif` 用例（本机装有 Office 时才跑）：它是 PowerShell 脚本与格式码的**唯一执法**，
桩件测不到那条分支，故宁可让它进套件而不是只活在一次性核验脚本里。
"""

from __future__ import annotations

import io
import struct
import zipfile

import pytest
from src.platform.providers.fileparser_dev import DevFileParser, FileParseError
from src.platform.providers.office_com import (
    OfficeComConverter,
    OfficeConvertError,
    container_problem,
)

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"


def _make_docx(paragraphs: list[str]) -> bytes:
    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{t}</w:t></w:r></w:p>' for t in paragraphs)
    doc = f'<?xml version="1.0"?><w:document xmlns:w="{_W}"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


def _make_pptx(slides: list[list[str]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, lines in enumerate(slides, 1):
            body = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in lines)
            xml = f'<?xml version="1.0"?><p:sld xmlns:a="{_A}" xmlns:p="{_P}"><p:cSld><p:spTree>{body}</p:spTree></p:cSld></p:sld>'
            z.writestr(f"ppt/slides/slide{i}.xml", xml)
    return buf.getvalue()


def _make_pdf(text: str) -> bytes:
    """stdlib 现场拼一份结构合法的单页 PDF（带正确 xref），供 pypdf 抽取——不依赖任何外部库写 PDF。"""
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Count 1/Kids[3 0 R]>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
    ]
    stream = f"BT /F1 12 Tf 20 150 Td ({text}) Tj ET".encode()
    objs.append(b"<</Length " + str(len(stream)).encode() + b">>stream\n" + stream + b"\nendstream")
    objs.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj".encode() + o + b"endobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer<</Size {len(objs) + 1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF".encode()
    return bytes(out)


def _ole_header_bytes() -> bytes:
    """现场拼一份**结构合法的最小 OLE 头**（512 字节）：只用于验容器真伪门的放行侧，不含真流。"""
    head = bytearray(512)
    head[0:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    head[30:32] = struct.pack("<H", 9)  # sectorShift=9 → 512 字节扇区
    head[32:34] = struct.pack("<H", 6)  # miniSectorShift=6 → 64 字节迷你扇区
    return bytes(head)


class _StubOffice:
    """OfficeConverterPort 桩件：演练"转换成功／不可用／转换失败"三条路由，不启动任何 Office。"""

    def __init__(self, *, payload: bytes | None = None, error: Exception | None = None, available: bool = True) -> None:
        self._payload = payload
        self._error = error
        self._available = available
        self.calls: list[str] = []

    def available(self) -> bool:
        return self._available

    def convert(self, data: bytes, ext: str) -> tuple[str, bytes]:
        self.calls.append(ext)
        if self._error is not None:
            raise self._error
        assert self._payload is not None
        return ("docx", "pptx")[ext == "ppt"], self._payload


@pytest.fixture()
def parser() -> DevFileParser:
    return DevFileParser()


def test_supported_matrix(parser: DevFileParser) -> None:
    assert all(parser.supported(f"a{e}") for e in (".docx", ".pptx", ".pdf", ".txt", ".md"))
    # M4-ce 1c：legacy OLE 的 .doc/.ppt 进支持集（转换供给在位，抽取仍走 OOXML 同一支抽取器）
    assert all(parser.supported(f"a{e}") for e in (".doc", ".ppt"))
    # 电子表格仍不支持——缺的是表格正文抽取档（连 .xlsx 也没有），不是转换器；对齐口径属放行项⑧，不谎称能解
    assert not parser.supported("a.xls")
    assert not parser.supported("a.xlsx")
    assert not parser.supported("a.zip")


def test_container_gate_rejects_non_ole_before_launching_office() -> None:
    """容器真伪门是转换前的**唯一**闸门：Word 的 COM 打开太宽容（挂 .doc 名的纯文本也"读"得出文字），
    不先判容器就会把垃圾字节当正文入库——这条必须零 Office、跨平台确定性地拦住。"""
    converter = OfficeComConverter()
    for payload, needle in (
        (b"\xd0\xcf\x11\xe0notreallyadoc", "不足 OLE 头扇区"),  # 截断垃圾：实测曾被 Word 回收成乱码文字
        (b"plain text pretending to be a doc " * 30, "缺 OLE 复合文档魔数"),  # 纯文本挂 .doc 名
        (b"PK\x03\x04" + b"\x00" * 600, "缺 OLE 复合文档魔数"),  # OOXML zip 改名 .doc
        (_ole_header_bytes()[:30] + b"\x00" * 500, "扇区参数异常"),  # 有魔数但头已损坏
    ):
        with pytest.raises(OfficeConvertError, match=needle):
            converter.convert(payload, "doc")
    assert container_problem(_ole_header_bytes()) is None  # 合法头放行（真容器的往返见下方 skipif 用例）


def test_docx_extracts_paragraphs(parser: DevFileParser) -> None:
    parsed = parser.parse("policy.docx", _make_docx(["出差住宿标准五百元", "第二条交通高铁二等座"]))
    text = parsed.text  # ADR-009 起 parse 返回"正文＋页态"；非 PDF 格式一律 digital，抽取语义不变
    assert parsed.page_state == "digital"
    assert "出差住宿标准五百元" in text
    assert "第二条交通高铁二等座" in text
    assert text.index("住宿") < text.index("交通")  # 段落顺序保留


def test_pptx_extracts_all_slides_in_order(parser: DevFileParser) -> None:
    text = parser.parse("deck.pptx", _make_pptx([["标题页要点"], ["第二页正文"]])).text
    assert "标题页要点" in text and "第二页正文" in text
    assert text.index("标题页要点") < text.index("第二页正文")


def test_txt_and_md_decode_utf8(parser: DevFileParser) -> None:
    assert parser.parse("n.txt", "第一行\n第二行".encode()).text == "第一行\n第二行"
    assert parser.parse("r.md", "# 标题".encode("utf-8-sig")).text.startswith("# 标题")  # BOM 容忍


def test_pdf_text_layer_extracted(parser: DevFileParser) -> None:
    pytest.importorskip("pypdf", reason="可选 extra [documents]")
    # 用 ASCII 断言"文本层被抽出"这条路由——手写 PDF 未声明 UTF-16 文本编码，CJK 不参与本用例（真实 PDF 由 pypdf 正确解码）。
    parsed = parser.parse("doc.pdf", _make_pdf("allowance 500"))
    assert "allowance" in parsed.text and "500" in parsed.text
    # 文本层齐备＝digital 且**一次 OCR 都不发起**：有字可抽的文档不该付那份权重加载/CPU 成本
    assert parsed.page_state == "digital" and parsed.ocr_pages == ()


def test_non_text_bytes_do_not_silently_pass(parser: DevFileParser) -> None:
    with pytest.raises(FileParseError):  # 二进制垃圾当 .txt → 解码失败必须报错，不返回乱码入库
        parser.parse("x.txt", bytes(range(128, 256)))


def test_unsupported_and_corrupt_fail_loud(parser: DevFileParser) -> None:
    with pytest.raises(FileParseError):
        parser.parse("legacy.doc", b"\xd0\xcf\x11\xe0junk")  # OLE 头齐但截断＝容器真伪门拒（转换前）
    with pytest.raises(FileParseError):
        parser.parse("bad.xyz", b"hi")  # 未知扩展名，拒
    with pytest.raises(FileParseError):
        parser.parse("corrupt.pdf", b"definitely not a pdf")  # 非法 PDF，拒


# —— M4-ce 1c：legacy OLE 经本机 Office 转换后走同一支抽取器 ——


def test_ole_routes_through_converter_into_same_extractor() -> None:
    """.doc/.ppt 抽出的正文由**既有 `_docx_text`/`_pptx_text`** 消费：转换档只换容器，不另起第二套解析口径。"""
    doc_stub = _StubOffice(payload=_make_docx(["旧版制度第一条", "附件清单见后"]))
    text = DevFileParser(office=doc_stub).parse("old-policy.doc", _ole_header_bytes()).text
    assert "旧版制度第一条" in text and "附件清单见后" in text
    assert doc_stub.calls == ["doc"]

    ppt_stub = _StubOffice(payload=_make_pptx([["旧版评审首页"], ["第二页要点"]]))
    deck = DevFileParser(office=ppt_stub).parse("old-deck.ppt", _ole_header_bytes()).text
    assert "旧版评审首页" in deck and "第二页要点" in deck and "幻灯片 1" in deck
    assert ppt_stub.calls == ["ppt"]


def test_ole_without_local_office_fails_loud_and_points_to_external_converter() -> None:
    stub = _StubOffice(available=False)
    with pytest.raises(FileParseError, match="外部转换器"):
        DevFileParser(office=stub).parse("old.doc", _ole_header_bytes())
    assert stub.calls == []  # 不可用＝连转换都不发起（不拿半个 Office 进程赌运气）


def test_ole_conversion_error_maps_to_file_parse_error() -> None:
    """COM 侧任何失败（超时/打不开/产出空文件）都必须以 FileParseError 上浮→门面 4xx，绝不退化成空文本。"""
    stub = _StubOffice(error=OfficeConvertError("Office 未产出 OOXML（退出码 3）：模拟"))
    with pytest.raises(FileParseError, match="模拟"):
        DevFileParser(office=stub).parse("old.doc", _ole_header_bytes())


def test_temp_tree_cleanup_reports_reality(tmp_path: object) -> None:
    """转换临时目录**可判定地**删净：`remove_tree` 返回布尔而不是把"没删净"当"已删"（真实核验抓出的残留缺陷）。"""
    from pathlib import Path

    from src.platform.providers import office_com

    target = Path(tmp_path) / "conv"
    target.mkdir()
    (target / "in.doc").write_bytes(b"x")
    assert office_com.remove_tree(target) is True
    assert not target.exists()


def test_sweep_stale_only_touches_own_aged_prefix(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    """清扫的两条自限（前缀 + 超龄）：在途转换与别人的临时目录都不能碰。"""
    import os
    import time
    from pathlib import Path

    from src.platform.providers import office_com

    root = Path(tmp_path)
    monkeypatch.setattr(office_com.tempfile, "gettempdir", lambda: str(root))
    fresh = root / f"{office_com.TEMP_PREFIX}fresh"
    aged = root / f"{office_com.TEMP_PREFIX}aged"
    stranger = root / "not-ours"
    for d in (fresh, aged, stranger):
        d.mkdir()
        (d / "file").write_bytes(b"x")
    old = time.time() - office_com.STALE_AFTER_SECONDS - 10
    os.utime(aged, (old, old))
    assert office_com.sweep_stale() == 1  # 只清那一个超龄的
    assert fresh.exists() and stranger.exists() and not aged.exists()


@pytest.mark.skipif(not OfficeComConverter().available(), reason="需本机 Windows + PowerShell + Office（非 CI 档）")
def test_real_ole_roundtrip_through_local_office() -> None:
    """真实往返（本机装有 Office 时才跑）：现场用 Word 造一份真 .doc → COM 转换 → 抽出中文正文。

    这是 PowerShell 脚本体（COM 参数、`SaveAs` 格式码 12）的唯一执法：桩件测不到那条分支，
    写错的格式码会让所有 .doc 上传静默变成 4xx，而套件里没有任何一例能发现。
    """
    import os
    import subprocess
    import tempfile
    from pathlib import Path

    script = (
        "$ErrorActionPreference='Stop';$app=New-Object -ComObject Word.Application;"
        "$app.Visible=$false;$doc=$app.Documents.Add();$sel=$app.Selection;"
        "$sel.TypeText($env:L1);$sel.TypeParagraph();$sel.TypeText($env:L2);"
        "$doc.SaveAs([ref]$env:OUT,[ref]0);$app.Quit()"
    )
    tmp = Path(tempfile.mkdtemp(prefix="ole-fixture-"))
    try:
        out = tmp / "real.doc"
        env = {**os.environ, "OUT": str(out), "L1": "旧版差旅报销规定", "L2": "第三条 市内交通费每日上限八十元"}
        proc = subprocess.run(  # 常量脚本 + 经 env 传文本（外部输入不拼命令行）
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            env=env,
            timeout=180,
        )
        if not out.exists():
            pytest.skip(f"本机 Word 未能造出 .doc 夹具（rc={proc.returncode}）：{proc.stderr[:200]!r}")
        data = out.read_bytes()
        assert data.startswith(bytes.fromhex("d0cf11e0"))  # 真 OLE 容器，非 zip
        text = DevFileParser().parse("real.doc", data).text
        assert "旧版差旅报销规定" in text and "每日上限八十元" in text
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)

