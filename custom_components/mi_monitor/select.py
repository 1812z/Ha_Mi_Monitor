"""Select 平台：画面模式与输入切换。"""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MiMonitorCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

#: 画面模式选项（显示名 -> 设备值）
PICTURE_MODES: dict[str, int] = {
    "标准": 14,
    "游戏": 10,
    "电影": 9,
}

#: 输入源选项（显示名 -> 设备值）
INPUT_SOURCES: dict[str, int] = {
    "HDMI 1": 23,
    "HDMI 2": 24,
    "DP": 29,
    "USB-C": 30,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MiMonitorCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            MiMonitorPictureModeSelect(coordinator, entry),
            MiMonitorInputSourceSelect(coordinator, entry),
        ]
    )


class MiMonitorPictureModeSelect(CoordinatorEntity[MiMonitorCoordinator], SelectEntity):
    """画面模式：标准 / 游戏 / 电影。"""

    _attr_options = list(PICTURE_MODES)
    _attr_icon = "mdi:television-classic"

    def __init__(self, coordinator: MiMonitorCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_name = f"{coordinator.client.ip} 画面模式"
        self._attr_unique_id = f"{entry.entry_id}_picture_mode"
        self._attr_device_info = coordinator.device_info

    @property
    def current_option(self) -> str | None:
        if not self.coordinator.data:
            return None
        raw = self.coordinator.data.get("picture_mode")
        reverse = {str(v): k for k, v in PICTURE_MODES.items()}
        return reverse.get(str(raw).strip()) if raw is not None else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.client.set_picture_mode(option)
        await self.coordinator.async_request_refresh()


class MiMonitorInputSourceSelect(CoordinatorEntity[MiMonitorCoordinator], SelectEntity):
    """输入切换：HDMI 1 / HDMI 2 / DP / USB-C。"""

    _attr_options = list(INPUT_SOURCES)
    _attr_icon = "mdi:video-input-hdmi"

    def __init__(self, coordinator: MiMonitorCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_name = f"{coordinator.client.ip} 输入源"
        self._attr_unique_id = f"{entry.entry_id}_input_source"
        self._attr_device_info = coordinator.device_info

    @property
    def current_option(self) -> str | None:
        if not self.coordinator.data:
            return None
        raw = self.coordinator.data.get("mitv.tvplayer.hdmi.last.source")
        reverse = {str(v): k for k, v in INPUT_SOURCES.items()}
        return reverse.get(str(raw).strip()) if raw is not None else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.client.set_input_source(option)
        await self.coordinator.async_request_refresh()
