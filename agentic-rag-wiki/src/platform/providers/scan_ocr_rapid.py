"""扫描版 PDF 的页级正文供给（FR-ING-8 第①刀旁路的真实现；PRD 放行项⑦ 收口）。

这一档做两件本机可决的事（用户定档"RapidOCR（onnxruntime）优先"）：
①**光栅化**＝`pypdfium2`（BSD-3-Clause/Apache-2.0）把 PDF 页渲成位图——刻意排除 PyMuPDF（AGPL，
撞 `deps_governance` 的 copyleft 黑名单 `_DENY_TOKENS`，即本项目的许可证门会判红）；
②**认字**＝`rapidocr-onnxruntime`（Apache-2.0）＋ `onnxruntime`（MIT），det/rec/cls 三个 ONNX 权重
随 wheel 分发＝**零模型下载、零外呼**，密级扫描件不出机（R11 口径）。

边界与诚实标注：
- `rapidocr_onnxruntime` / `pypdfium2` / `numpy` 只允许 import 于本文件（§6 禁令1 的同族口径）。
  `harness/sdk_blacklist.txt` 属禁改区，故该约束由 `tests/contract/test_scan_ocr.py` 的 AST 锁执法（承 M4-ce 1c）。
- `available()` 是**能力探测**，不是配置旋钮（承 M4-aj..au 幻影旋钮家族的教训：探测在位即不新增 Settings 字段）。
- 认不出字＝返回该页空串，由调用侧（`DevFileParser`）判"失败页"并排除出索引；本档**绝不**伪造文字、
  也绝不把"缺依赖"降级成空串（那等于宣称"这页本来没字"）。
- 本机实测（CPU、`RENDER_SCALE=2.0`、约 960×308 的合成页）：引擎初始化约 0.5s（每实例一次）、
  单页识别约 1.1s。夜窗批调度与逐页位点账需真实队列/PG＝放行项，不在本档自造。
"""

from __future__ import annotations

from importlib.util import find_spec
from typing import Any

from src.platform.providers.errors import ProviderNotConfigured

RENDER_SCALE = 2.0  # 约 144 DPI：实测中文/英文混排逐字正确；再放大只增耗时不增字
_EXTRA_HINT = "pip install 'agentic-rag-wiki[ocr]'"


class ScanOcrUnavailable(ProviderNotConfigured):
    """OCR 供给缺位/不可用（依赖未装、PDF 无法渲图、页码越界）——由解析档转 `FileParseError`→4xx。"""


class RapidScanOcr:
    """`ScanOcrPort` 执行档：按页码把 PDF 页认成文字（RapidOCR + pypdfium2，纯本机 CPU）。

    引擎是**惰性单例**（首次识别才加载权重）：`supported()`/文本层能抽出的文档不该付这份显存/CPU 成本。
    """

    def __init__(self, *, scale: float = RENDER_SCALE) -> None:
        self._scale = scale
        self._engine: Any | None = None

    @staticmethod
    def available() -> bool:
        """两个供给都在位才算可用（只装了 OCR、没渲染器＝渲不出图，一样是半条通道）。"""
        return find_spec("rapidocr_onnxruntime") is not None and find_spec("pypdfium2") is not None

    def _get_engine(self) -> Any:
        if self._engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except ModuleNotFoundError as exc:  # pragma: no cover — 取决于环境
                raise ScanOcrUnavailable(f"扫描页识别需 rapidocr-onnxruntime（{_EXTRA_HINT}）") from exc
            self._engine = RapidOCR()
        return self._engine

    def recognize_pages(self, data: bytes, page_indices: list[int]) -> dict[int, str]:
        """逐页认字：返回 `{页码(0 基): 该页文本}`；认不出字的页=空串（失败页判定在调用侧，不在这里吞）。"""
        if not page_indices:
            return {}
        try:
            import numpy as np
            import pypdfium2 as pdfium
        except ModuleNotFoundError as exc:  # pragma: no cover — 取决于环境
            raise ScanOcrUnavailable(f"扫描页光栅化需 pypdfium2（{_EXTRA_HINT}）") from exc
        engine = self._get_engine()
        try:
            doc = pdfium.PdfDocument(data)
        except Exception as exc:  # pdfium 对损坏/加密件抛多种内部错，统一转本档错并保留原因
            raise ScanOcrUnavailable(f"PDF 光栅化失败（疑损坏或加密）：{exc}") from exc
        out: dict[int, str] = {}
        try:
            total = len(doc)
            for idx in page_indices:
                if not 0 <= idx < total:
                    raise ScanOcrUnavailable(f"请求识别页码 {idx} 越出该 PDF 页数 {total}")
                bitmap = np.asarray(doc[idx].render(scale=self._scale).to_pil())
                result, _elapsed = engine(bitmap)
                out[idx] = "\n".join(str(line[1]) for line in (result or []))
        finally:
            doc.close()
        return out
