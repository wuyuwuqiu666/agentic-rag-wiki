"""legacy OLE Office（`.doc`/`.ppt`）→ OOXML（`.docx`/`.pptx`）的本机转换档（放行项㉒的离线可决半边）。

为什么要有这一档：`DevFileParser` 只吃 OOXML zip（stdlib `zipfile`+`xml.etree`），旧版二进制 OLE 容器不是 zip，
纯 stdlib 抽不出正文。企业口径要么引第三方解析库（越 T4：本刀零新 pip 依赖），要么走"外部转换器"——
本机装了 Office，Word/PowerPoint 自身就是最好的转换器，经 PowerShell 的 COM 晚绑定调用即可，
**不进 python 依赖树**（stdlib `subprocess` + 系统 `powershell.exe`）。

执行边界（C2 四要素，本档自带）：
- **超时**：单次转换 `TIMEOUT_SECONDS`，超时 kill 并 fail-loud（不把"卡住的 Office"当成慢）。
- **文件边界**：输入/输出只落在 `tempfile` 建的**一次性私有临时目录**，转换结束即 `remove_tree` 删除（带重试：
  Office 在 `Quit()` 后还要 1–2 秒才放句柄，单次 `rmtree` 会静默留下残留）；仍删不净的由下次转换开头的
  `sweep_stale` 按前缀+超龄清扫。**绝不写摄取根或产物目录**（防 M4-ce 1b 的自灌围栏被绕过）。
- **网络**：零外呼——纯本机进程间调用，密级语料不出机。
- **注入面**：PowerShell 脚本是**常量字符串**，路径与档位一律经环境变量传入（`$env:`），
  文件名/正文内容永不拼进命令行（外部输入不可进 shell 命令串，T2）。

诚实边界（勿误读为"所有旧格式全能吃"）：
- **先过容器真伪门**（`container_problem`）才交 Office：改名的纯文本/RTF/docx 会被明确拒绝，不会"读"出一段垃圾文字入库。
- 只覆盖 `.doc`/`.ppt`。**`.xls` 不在此列且不是 COM 问题**：dev 档连现代 `.xlsx` 都没有电子表格正文抽取器，
  先补表格文本档并把口径对齐 FR-ING-7 表格原子化（放行项⑧）才谈得上 `.xls`，本刀不顺手造一份会撞口径的 dump。
- COM 只在 Windows + 装有对应 Office 时可用：`available()` 只判平台与 `powershell` 在位（不启动 Office，
  避免每次预筛都拉一个进程）；真不可用/转换失败一律抛 `OfficeConvertError`，由 `DevFileParser` 转 `FileParseError`→4xx。
- 企业档换 LibreOffice/pandoc 等命令行转换器＝换实现（G5），`FileParserPort` 与摄取链不感知。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# 支持的 OLE 扩展名 → (COM 档位, 目标 OOXML 扩展名)
OLE_MAP: dict[str, tuple[str, str]] = {"doc": ("word", "docx"), "ppt": ("powerpoint", "pptx")}

TIMEOUT_SECONDS = 90  # 单文档转换上限：超时即视为失败（宁可报错，不无限等一个弹对话框的 Office）

# 常量脚本：绝不插值外部输入；kind/路径经 $env: 读取。Word 存 wdFormatXMLDocument=12，PPT 存 ppSaveAsOpenXMLPresentation=24。
_PS_SCRIPT = """
$ErrorActionPreference = 'Stop'
$kind = $env:RAG_OFFICE_KIND
$in = $env:RAG_OFFICE_IN
$out = $env:RAG_OFFICE_OUT
$app = $null
try {
  if ($kind -eq 'word') {
    $app = New-Object -ComObject Word.Application
    $app.Visible = $false
    $app.DisplayAlerts = 0
    $doc = $app.Documents.Open($in, $false, $true)
    if ($null -eq $doc) { throw 'Word 未打开该文档' }
    $doc.SaveAs([ref]$out, [ref]12)
  } elseif ($kind -eq 'powerpoint') {
    $app = New-Object -ComObject PowerPoint.Application
    $pres = $app.Presentations.Open($in, $true, $false, $false)
    if ($null -eq $pres) { throw 'PowerPoint 未打开该演示文稿' }
    $pres.SaveAs($out, 24)
  } else {
    throw ('未知档位 ' + $kind)
  }
  exit 0
} catch {
  [Console]::Error.WriteLine('OFFICE-COM-FAIL ' + $_.Exception.Message)
  exit 3
} finally {
  if ($null -ne $app) { try { $app.Quit() } catch { } }
}
"""


class OfficeConvertError(RuntimeError):
    """本机 Office 转换不可用或转换失败——由 `DevFileParser` 转成 `FileParseError`（门面 4xx），绝不返回空文本。"""


TEMP_PREFIX = "rag-office-"
STALE_AFTER_SECONDS = 600  # 远超 TIMEOUT_SECONDS：一次在途转换的目录不可能这么老，故不会误扫别人的现场


def remove_tree(path: Path, *, attempts: int = 5, pause: float = 0.4) -> bool:
    """带重试地删掉一次性目录，返回**是否删净**。

    为什么要重试：Office 在 `Quit()` 之后还要 1–2 秒才释放文件句柄（实测：转换刚结束时 `rag-office-*` 仍在，
    4 秒后为 0）。单次 `rmtree(ignore_errors=True)` 会把"没删净"静默吞成"已删"，日积月累就是每次上传留一个临时目录。
    删净与否在这里是可判定的事实，绝不假装成功；仍删不净的交给 `sweep_stale` 下次收。
    """
    for attempt in range(attempts):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return True
        if attempt < attempts - 1:
            time.sleep(pause)
    return not path.exists()


def sweep_stale(now: float | None = None) -> int:
    """清扫历史遗留的转换目录（上次因句柄未释放而没删净的那些），返回清掉的个数。

    两条自限：只碰本档自己的 `TEMP_PREFIX` 前缀；只碰 mtime 早于 `STALE_AFTER_SECONDS` 的目录——
    并发进行中的转换（含 90s 超时上限）因此不会被误删。
    """
    cutoff = (time.time() if now is None else now) - STALE_AFTER_SECONDS
    removed = 0
    for candidate in Path(tempfile.gettempdir()).glob(TEMP_PREFIX + "*"):
        try:
            if candidate.is_dir() and candidate.stat().st_mtime < cutoff:
                shutil.rmtree(candidate, ignore_errors=True)
                removed += 1
        except OSError:  # 目录正被别的过程占用/已消失：跳过它，不影响本次转换
            continue
    return removed


_OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_MIN_OLE_BYTES = 512  # 头扇区本身即 512 字节；不足这个长度绝不可能是容器

# 容器真伪门（必读，勿当冗余删掉）：Word/PowerPoint 的 COM 打开**非常宽容**——纯文本、RTF、改名的 docx，
# 只要扩展名是 .doc/.ppt 也照样"读"出一段文字（实测 `b"\xd0\xcf\x11\xe0notreallyadoc"` → `邢鄋otreallyadoc`，
# 一段随机文本 → 原样返回）。若不做这一步，垃圾字节就会以"正文"身份进索引并出现在答案里＝静默入库，
# 正是 FileParserPort docstring 要拦的那类。本门只判**头部结构**（零依赖、不解析流），不是完整 OLE 解析器：
# 容器结构合法但内容损坏仍可能转换失败，那条路走 `OfficeConvertError`，同样是 fail-loud。
def container_problem(data: bytes) -> str | None:
    """返回 `None`＝按头部结构判为真 OLE 复合文档；否则返回拒绝原因（供门面 4xx 文案）。"""
    if len(data) < _MIN_OLE_BYTES:
        return f"长度 {len(data)} 字节不足 OLE 头扇区（≥{_MIN_OLE_BYTES}），不可能是旧版复合文档"
    if not data.startswith(_OLE_MAGIC):
        return "缺 OLE 复合文档魔数 d0cf11e0a1b11ae1（疑为改名的纯文本/RTF/docx，非旧版二进制容器）"
    sector_shift = int.from_bytes(data[30:32], "little")
    mini_shift = int.from_bytes(data[32:34], "little")
    if sector_shift not in (9, 12) or mini_shift != 6:
        return f"扇区参数异常（sectorShift={sector_shift}、miniSectorShift={mini_shift}），容器已损坏"
    return None



class OfficeComConverter:
    """`FileParserPort` 的转换供给档：把 OLE 字节交给本机 Word/PowerPoint 另存为 OOXML，读回字节。"""

    def available(self) -> bool:
        """平台级可用性预筛（不启动 Office）：仅 Windows + `powershell` 在位时才谈转换。"""
        return sys.platform == "win32" and shutil.which("powershell") is not None

    def convert(self, data: bytes, ext: str) -> tuple[str, bytes]:
        """返回 `(目标扩展名, OOXML 字节)`；任何失败路径都抛 `OfficeConvertError`。"""
        spec = OLE_MAP.get(ext)
        if spec is None:
            raise OfficeConvertError(f"非本档转换范围的文件类型 .{ext}")
        problem = container_problem(data)
        if problem is not None:  # 先判容器真伪，再谈转换（见上方注释：COM 太宽容，垃圾也会"读"出文字）
            raise OfficeConvertError(problem)
        kind, target_ext = spec
        sweep_stale()  # 先把上次没删净的历史目录收掉（句柄释放是秒级的事，交给下一次清扫而不是假装成功）
        tmp = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
        try:
            src = tmp / f"in.{ext}"
            dst = tmp / f"out.{target_ext}"
            src.write_bytes(data)
            env = {**os.environ, "RAG_OFFICE_KIND": kind, "RAG_OFFICE_IN": str(src), "RAG_OFFICE_OUT": str(dst)}
            try:
                proc = subprocess.run(  # noqa: S603 — 固定可执行文件名 + 常量脚本参数，无 shell 拼接
                    ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _PS_SCRIPT],
                    capture_output=True,
                    timeout=TIMEOUT_SECONDS,
                    env=env,
                )
            except subprocess.TimeoutExpired as exc:
                raise OfficeConvertError(f"Office 转换超时（>{TIMEOUT_SECONDS}s），未产出 OOXML") from exc
            except OSError as exc:
                raise OfficeConvertError(f"无法启动本机 PowerShell：{exc}") from exc
            if not dst.exists():
                detail = (proc.stderr.decode("utf-8", "replace") or proc.stdout.decode("utf-8", "replace")).strip()
                raise OfficeConvertError(f"Office 未产出 OOXML（退出码 {proc.returncode}）：{detail[:400] or '无错误输出'}")
            out_bytes = dst.read_bytes()
            if not out_bytes:
                raise OfficeConvertError("Office 产出的 OOXML 为空文件")
            return target_ext, out_bytes
        finally:
            remove_tree(tmp)  # 文件边界：一次性目录；删不净的由下次 `sweep_stale` 收（产物绝不留存、更不进摄取根）
