"""gateway / 组合根：装配 + 应用外壳。"""

from __future__ import annotations

from src.platform.gateway.app import App
from src.platform.gateway.composition import Container, build_container
from src.platform.gateway.inflight import InflightTracker

__all__ = ["App", "Container", "InflightTracker", "build_container"]
