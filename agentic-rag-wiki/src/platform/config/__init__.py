"""装配层（PRD §5 config）：env 读取、provider 选择、阈值常量引用。

E2 口径：dev/staging/prod 只差本层配置，代码零分支；缺必需 env 即启动失败（fail-fast）。
"""

from src.platform.config.settings import ProviderChoices, Settings, get_settings

__all__ = ["ProviderChoices", "Settings", "get_settings"]
