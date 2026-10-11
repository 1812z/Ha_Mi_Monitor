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

from .adb_client import AdbClient, derive_display_name, resolve_adb_key
from .const import CONF_INFO, DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=30)


class MiMonitorCoordinator(DataUpdateCoordinator[dict[str, object]]):
    """单台显示器的状态协调器：定期回读全部受控参数。"""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, key_path: str | None = None
    ) -> None:
        host = str(entry.data.get(CONF_HOST) or "")
        super().__init__(
            hass,
            _LOGGER,
            name=f"Mi Monitor {host}",
            update_interval=SCAN_INTERVAL,
        )
        self.host = host
        self.client = AdbClient(host, key_path=key_path)
        self.connected = False
        #: 配置时探测到的设备信息（型号/固件/分辨率…），后续刷新会更新
        self.info: dict[str, str] = dict(entry.data.get(CONF_INFO) or {})
        self._attr_device_info = _build_device_info(host, self.info)

    @property
    def device_info(self) -> dict[str, object]:
        """供实体挂到同一设备下（HA「设备信息」面板显示型号/固件等）。"""
        return self._attr_device_info

    @property
    def display_name(self) -> str:
        """人类可读名称：优先中文市场名，其次型号，最后退回 IP。"""
        return (
            derive_display_name(self.info)
            or (self.info.get("model") or "").strip()
            or self.host
        )

    def _apply_info(self, info: dict[str, str]) -> None:
        """合并新探测到的信息并刷新设备信息。"""
        if not info:
            return
        self.info.update(info)
        self._attr_device_info = _build_device_info(self.host, self.info)
        # 让已创建的实体把新的 device_info 同步到注册表
        if self.hass is not None:
            self.async_update_listeners()

    async def _async_update_data(self) -> dict[str, object]:
        """回读状态；连接失败抛 UpdateFailed（实体进入 unavailable）。"""
        try:
            ok, state = await self.client.connect()
            if not ok:
                self.connected = False
                raise UpdateFailed(f"显示器不可达：{state}")
            self.connected = True

            # 首次拿到信息后补齐设备信息（型号等）；失败不影响参数刷新
            if not self.info.get("model"):
                try:
                    self._apply_info(await self.client.get_device_info())
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.debug("读取设备信息失败: %s", exc)

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


def _build_device_info(host: str, info: dict[str, str]) -> dict[str, object]:
    """构造 HA 的 device_info（决定「设备信息」面板里显示什么）。

    参考 Mimonitor_Toolbox 读 ``ro.product.model`` 识别机型的做法，这里把
    型号、固件、Android 版本、分辨率、序列号等都暴露给 HA。
    """
    display = derive_display_name(info) or (info.get("model") or "").strip() or f"Mi Monitor {host}"
    device: dict[str, object] = {
        "identifiers": {(DOMAIN, host)},
        # 设备名用型号，而不是 IP
        "name": display,
        "manufacturer": (info.get("manufacturer") or info.get("brand") or "Xiaomi").title(),
        "model": (info.get("model") or "Mi Monitor").strip(),
        "configuration_url": f"http://{host}",
    }
    if info.get("firmware"):
        device["sw_version"] = info["firmware"]
    if info.get("serial"):
        device["serial_number"] = info["serial"]
    if info.get("android_version"):
        device["hw_version"] = f"Android {info['android_version']}"
    return device


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """设置一个配置条目（一台显示器）。"""
    # 密钥解析/生成是阻塞操作（RSA 生成），放 executor
    key_path = await hass.async_add_executor_job(resolve_adb_key, hass)
    coordinator = MiMonitorCoordinator(hass, entry, key_path=key_path)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    _sync_entry_with_device(hass, entry, coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _sync_entry_with_device(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: MiMonitorCoordinator
) -> None:
    """把探测到的信息写回条目，并把条目名从 IP 修正为型号。

    老版本添加的条目名称是 ``Mi Monitor <ip>``，这里升级成型号（例如
    ``MiTV-MFFU1``）；用户手动改过名字（不等于旧默认值）就不动。
    """
    info = coordinator.info
    updates: dict[str, object] = {}

    old_default_titles = {f"Mi Monitor {coordinator.host}", coordinator.host, ""}
    new_title = derive_display_name(info) or (info.get("model") or "").strip()
    if new_title and entry.title in old_default_titles:
        updates["title"] = new_title

    if info and entry.data.get(CONF_INFO) != info:
        updates["data"] = {**entry.data, CONF_INFO: info}

    if updates:
        hass.config_entries.async_update_entry(entry, **updates)


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
