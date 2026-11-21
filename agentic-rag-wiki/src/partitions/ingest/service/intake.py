"""FR-ING-8 三态探测的准入分流纯规则（第⓪刀，无 IO）。

页面级信号（digital/scanned/mixed）→ 车道决策 + 质量门：
- digital → 主车道（正文已在，直连 canonicalize）。
- scanned/mixed → OCR 旁路（须先经 `OcrPort.recognize` 取正文；未装配供给即 fail-loud，禁当 digital 静默入库）。
- 质量门：OCR 产出空/退化 → 判"失败页"，**不进索引**（交隔离，绝不把未识别扫描件当正文入库）。

真·逐页探测（读 PDF 每页文本层密度定 page_state）与"mixed 按页拆、canonical 期合并"依赖页级正文载体，属真实摄取供给=放行项；
本模块只锁定可离线机检的分流谓词与质量门口径（dev PassthroughOcr 演练同一路径）。
"""

from __future__ import annotations

from src.platform.types.document import PageState

# scanned/mixed 走 OCR 旁路；digital 主车道（三态之外的取值保守按主车道，避免误触 OCR）。
_OCR_LANES: frozenset[str] = frozenset({"scanned", "mixed"})


def needs_ocr(page_state: PageState) -> bool:
    """第⓪刀：该页态是否必须经 OCR 旁路取正文。"""
    return page_state in _OCR_LANES


def ocr_output_ok(text: str, min_chars: int = 1) -> bool:
    """质量门：OCR 抽到的正文是否够格进索引（strip 后长度达阈，空/纯空白=失败页）。"""
    return len(text.strip()) >= min_chars
