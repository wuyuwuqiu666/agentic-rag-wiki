"""EmbeddingPort 执行档主档：本地 Qwen3-Embedding-0.6B（ADR-004/005）。

依赖（sentence-transformers + cu128 torch）惰性 import；缺失即 fail-loud，不回退 dev。
NFR-8：batch 越显存安全上界时抛 VramBudgetExceeded，由调用方降批重放（不静默落系统内存）。
"""

from __future__ import annotations

from src.platform.providers.errors import ProviderNotConfigured, VramBudgetExceeded


class LocalEmbedding:
    def __init__(
        self,
        model_id: str = "qwen3-embedding-0.6b",
        dim: int = 1024,
        batch_safe: int = 32,
        model_dir: str | None = None,
        input_token_limit: int = 32768,
    ) -> None:
        self._model_id = model_id
        self._dim = dim
        self._batch_safe = batch_safe
        # ADR-004：ModelScope 落盘权重目录；给定则 SentenceTransformer 从此 cache_folder 加载，缺省回退框架默认缓存。
        self._model_dir = model_dir
        # ADR-004：Qwen3-Embedding 32K 输入上界（NFR-8 门限唯一口径源=Settings.local_input_token_limit，经 composition 注入）。
        self._input_token_limit = input_token_limit
        self._model: object | None = None

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def model_dir(self) -> str | None:
        return self._model_dir

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def input_token_limit(self) -> int:
        return self._input_token_limit


    def _load(self) -> object:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer  # noqa: PLC0415
            except ImportError as exc:  # pragma: no cover - 依赖缺失路径
                raise ProviderNotConfigured(
                    "本地 embedding 需 sentence-transformers + cu128 torch（NFR-6 Blackwell）"
                ) from exc
            if self._model_dir:
                self._model = SentenceTransformer(self._model_id, cache_folder=self._model_dir)
            else:
                self._model = SentenceTransformer(self._model_id)
        return self._model


    def embed(self, texts: list[str]) -> list[list[float]]:
        if len(texts) > self._batch_safe:
            raise VramBudgetExceeded(batch=len(texts), safe_batch=self._batch_safe)
        model = self._load()
        vecs = model.encode(texts, normalize_embeddings=True)  # type: ignore[attr-defined]
        return [[float(x) for x in row] for row in vecs]


def safe_batch_for_vram(vram_mb: int, per_batch_mb: int, *, safety_factor: float) -> int:
    """NFR-8：`物理显存 × 安全系数` 反推 batch 安全上界（可复算、可对账）。

    离线纯函数——**不探测真机显存**（torch.cuda 实测=实机放行档），仅按调用方给定的显存预算与每批占用
    反推；`safety_factor` 系 `Settings.vram_safety_factor`（默认 0.9）的真实消费点。越界仍由 `embed()`
    的 `VramBudgetExceeded` fail-loud 兜底（NFR-8 禁静默降速）。外部输入先校验（T2），非法即抛 `ValueError`。
    """
    if vram_mb <= 0 or per_batch_mb <= 0:
        raise ValueError(f"显存预算须为正：vram_mb={vram_mb} per_batch_mb={per_batch_mb}")
    if not 0.0 < safety_factor <= 1.0:
        raise ValueError(f"安全系数须落在 (0, 1]：safety_factor={safety_factor}")
    return int(vram_mb * safety_factor) // per_batch_mb
