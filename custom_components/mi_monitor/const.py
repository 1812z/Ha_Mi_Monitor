"""mi_monitor 集成常量。"""

from homeassistant.const import Platform

DOMAIN = "mi_monitor"

#: 配置条目里保存的探测到的设备信息（型号/固件/分辨率等）
CONF_INFO = "info"

PLATFORMS: list[Platform] = [
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
]
