"""Home Assistant 集成入口：小米显示器 ADB 控制。

- 一个配置条目 = 一台显示器（一个 IP）
- 可添加多台显示器（多个配置条目）
- 每台显示器独立维护一个 DataUpdateCoordinator
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .adb_client import AdbClient, resolve_adb_key
from .const import DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=30)


class MiMonitorCoordinator(DataUpdateCoordinator[dict[str, object]]):
    """单台显示器的状态协调器：定期回读全部受控参数。"""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, key_path: str | None = None
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"Mi Monitor {entry.data.get(CONF_HOST)}",
            update_interval=SCAN_INTERVAL,
        )
        host = str(entry.data.get(CONF_HOST) or "")
        self.client = AdbClient(host, key_path=key_path)
        self.connected = False
        self.model: str | None = None
        self._attr_device_info = {
            "identifiers": {(DOMAIN, host)},
            "name": f"Mi Monitor {host}",
            "manufacturer": "Xiaomi / Redmi",
            "model": "Mi Monitor (ADB)",
            "sw_version": "1.1.0",
        }

    @property
    def device_info(self) -> dict[str, object]:
        """供实体挂到同一设备下。"""
        return self._attr_device_info

    async def _async_update_data(self) -> dict[str, object]:
        """回读状态；连接失败抛 UpdateFailed（实体进入 unavailable）。"""
        try:
            ok, state = await self.client.connect()
            if not ok:
                self.connected = False
                raise UpdateFailed(f"显示器不可达：{state}")
            self.connected = True

            model = (await self.client.get_model()).strip()
            if model:
                self.model = model

            keys = [
                "picture_backlight",
                "xiaomi_picture_backlight",
                "picture_brightness",
                "picture_contrast",
                "picture_saturation",
                "picture_hue",
                "picture_sharpness",
                "picture_mode",
                "mitv.tvplayer.hdmi.last.source",
            ]
            values: dict[str, object] = {}
            for key in keys:
                try:
                    values[key] = (await self.client.get(key)).strip()
                except Exception:  # noqa: BLE001
                    values[key] = None
            return values
        except Exception as exc:
            self.connected = False
            raise UpdateFailed(str(exc)) from exc


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """设置一个配置条目（一台显示器）。"""
    # 密钥解析/生成是阻塞操作（RSA 生成），放 executor
    key_path = await hass.async_add_executor_job(resolve_adb_key, hass)
    coordinator = MiMonitorCoordinator(hass, entry, key_path=key_path)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载配置条目。"""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        try:
            coordinator = hass.data[DOMAIN].pop(entry.entry_id)
            await coordinator.client.disconnect()
        except KeyError:
            pass
    return unload_ok
