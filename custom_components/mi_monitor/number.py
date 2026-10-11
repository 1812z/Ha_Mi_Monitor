"""Number 平台：可调显示参数（背光/黑色级别/对比度/饱和度/色调/锐度）。"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MiMonitorCoordinator
from .const import DOMAIN

#: settings 键 -> (标签, 最小值, 最大值, 步进, 单位)
PARAM_DEFS: dict[str, tuple[str, int, int, int, str | None]] = {
    "picture_backlight": ("背光", 1, 100, 5, "%"),
    "picture_brightness": ("黑色级别", 0, 100, 5, None),
    "picture_contrast": ("对比度", 0, 100, 5, None),
    "picture_saturation": ("饱和度", 0, 100, 5, None),
    "picture_hue": ("色调", 0, 100, 5, None),
    "picture_sharpness": ("锐度", 0, 100, 1, None),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MiMonitorCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        MiMonitorNumber(coordinator, entry, key, *definition)
        for key, definition in PARAM_DEFS.items()
    ]
    async_add_entities(entities)


class MiMonitorNumber(CoordinatorEntity[MiMonitorCoordinator], NumberEntity):
    """一个可调显示参数。背光走 JNI 直写，其余走 settings put。"""

    def __init__(
        self,
        coordinator: MiMonitorCoordinator,
        entry: ConfigEntry,
        settings_key: str,
        label: str,
        min_value: int,
        max_value: int,
        step: int,
        unit: str | None,
    ) -> None:
        super().__init__(coordinator)
        self._settings_key = settings_key
        self._attr_name = f"{coordinator.display_name} {label}"
        self._attr_unique_id = f"{entry.entry_id}_{settings_key}"
        self._attr_has_entity_name = False
        self._attr_min_value = min_value
        self._attr_max_value = max_value
        self._attr_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = self._icon_for_key(settings_key)
        self._attr_device_info = coordinator.device_info
        self._attr_entity_category = (
            EntityCategory.CONFIG if settings_key == "picture_sharpness" else None
        )

    @staticmethod
    def _icon_for_key(key: str) -> str:
        return {
            "picture_backlight": "mdi:brightness-7",
            "picture_brightness": "mdi:brightness-4",
            "picture_contrast": "mdi:contrast-circle",
            "picture_saturation": "mdi:palette-outline",
            "picture_hue": "mdi:palette",
            "picture_sharpness": "mdi:knife-military",
        }.get(key, "mdi:monitor")

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        raw = self.coordinator.data.get(self._settings_key)
        if raw in (None, "", "null", "N/A"):
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        """用户调整时写入显示器（背光走 JNI + settings 双写）。"""
        target = int(round(value))
        if self._settings_key == "picture_backlight":
            await self.coordinator.client.set_backlight(target)
        else:
            await self.coordinator.client.set_plain(self._settings_key, target)
        # 立即把新值同步进协调器，避免界面回弹
        self.coordinator.data = dict(self.coordinator.data or {})
        self.coordinator.data[self._settings_key] = str(target)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
