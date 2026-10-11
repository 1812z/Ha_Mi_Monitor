"""mi_monitor 集成常量。"""

from homeassistant.const import Platform

DOMAIN = "mi_monitor"
PLATFORMS: list[Platform] = [
    Platform.NUMBER,
    Platform.SELECT,
]
