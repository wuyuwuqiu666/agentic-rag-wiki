"""OcrPort dev 档：**准入侧**直通（FR-ING-8 第⓪刀的质量门输入），不承担识别。

识别（图像页→文字）在解析边界供给侧完成——M4-cf 放行项⑦ 已落 `providers/scan_ocr_rapid.py`（本机 RapidOCR，
唯一摸过页字节处，产出 raw_text + page_state + 失败页账）。本档若自称"能 OCR"就是撒谎，也会让同一页被认第二遍
（两次识别＝同一份文档两种字＝口径分叉）；企业档在此缝换的是"读侧再取外部 OCR 结论"（G5 换实现），OcrPort 接口不变。
"""

from __future__ import annotations

from src.platform.types import Document


class PassthroughOcr:
    def recognize(self, document: Document) -> str:
        return document.raw_text
