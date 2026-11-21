"""ObjectStorePort filesystem 档：canonical 正文落盘（F1：事件信封只存指针）。

键 = `{doc_id}/{version}`；写入前净化键防目录穿越（T5 边界）。生产档=s3（端口不变）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path


class FileSystemObjectStore:
    def __init__(self, base_dir: str = "workspace/canonical-draft") -> None:
        self._root = Path(base_dir).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = key.replace("..", "_").strip("/").replace("\\", "/")
        return (self._root / safe).resolve()

    def put(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    @staticmethod
    def checksum(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()
