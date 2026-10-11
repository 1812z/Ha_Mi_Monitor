"""Sensor 平台：显示器基础信息（型号 / 固件 / 面板 / 分辨率 / 连接状态等）。

对应 HA 设备页上的「信息」列。这些值在配置时探测一次，之后随协调器刷新更新。
"""

from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MiMonitorCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

#: 设备信息字段 -> 传感器描述
#: 字段名来自 adb_client.DEVICE_PROPERTIES（配置时批量探测）
INFO_SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="model",
        name="型号",
        icon="mdi:monitor",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="firmware",
        name="固件版本",
        icon="mdi:package-variant-closed",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="android_version",
        name="Android 版本",
        icon="mdi:android",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="display_size",
        name="面板分辨率",
        icon="mdi:monitor-screenshot",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="panel_type",
        name="面板类型",
        icon="mdi:lcd",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="chipset",
        name="芯片平台",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="serial",
        name="序列号",
        icon="mdi:barcode",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="host",
        name="IP 地址",
        icon="mdi:ip-network",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

#: 连接状态传感器（实体可用性一目了然）
CONNECTION_SENSOR = SensorEntityDescription(
    key="connection_state",
    name="连接状态",
    icon="mdi:lan-connect",
    device_class=SensorDeviceClass.ENUM,
    options=["connected", "disconnected"],
    entity_category=EntityCategory.DIAGNOSTIC,
)

#: 当前画面模式（可读值，便于自动化/仪表盘展示）
PICTURE_MODE_SENSOR = SensorEntityDescription(
    key="picture_mode_text",
    name="当前画面模式",
    icon="mdi:television-classic",
    entity_category=EntityCategory.DIAGNOSTIC,
)

#: 当前输入源（可读值）
INPUT_SOURCE_SENSOR = SensorEntityDescription(
    key="input_source_text",
    name="当前输入源",
    icon="mdi:video-input-hdmi",
    entity_category=EntityCategory.DIAGNOSTIC,
)

#: 设备值 -> 可读名
_PICTURE_MODE_NAMES = {"14": "标准", "10": "游戏", "9": "电影"}
_INPUT_SOURCE_NAMES = {"23": "HDMI 1", "24": "HDMI 2", "29": "DP", "30": "USB-C"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MiMonitorCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        MiMonitorInfoSensor(coordinator, entry, description)
        for description in INFO_SENSORS
    ]
    entities.append(MiMonitorConnectionSensor(coordinator, entry, CONNECTION_SENSOR))
    entities.append(MiMonitorPictureModeSensor(coordinator, entry, PICTURE_MODE_SENSOR))
    entities.append(MiMonitorInputSourceSensor(coordinator, entry, INPUT_SOURCE_SENSOR))
    async_add_entities(entities)


class MiMonitorInfoSensor(CoordinatorEntity[MiMonitorCoordinator], SensorEntity):
    """一个设备信息字段。"""

    entity_description: SensorEntityDescription

    def __init__(
        self,
        coordinator: MiMonitorCoordinator,
        entry: ConfigEntry,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_info_{description.key}"
        # 实体名带型号，多个显示器时容易区分
        self._attr_name = f"{coordinator.display_name} {description.name}"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> str | None:
        if self.entity_description.key == "host":
            return self.coordinator.host
        value = self.coordinator.info.get(self.entity_description.key)
        return value or None


class MiMonitorConnectionSensor(CoordinatorEntity[MiMonitorCoordinator], SensorEntity):
    """连接状态：connected / disconnected。"""

    entity_description = CONNECTION_SENSOR

    def __init__(self, coordinator: MiMonitorCoordinator, entry: ConfigEntry,
                 description: SensorEntityDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_connection"
        self._attr_name = f"{coordinator.display_name} 连接状态"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> str:
        return "connected" if self.coordinator.connected else "disconnected"

    @property
    def available(self) -> bool:
        # 状态本身就是可用性，所以永远可用
        return True


class _CoordinatorValueSensor(CoordinatorEntity[MiMonitorCoordinator], SensorEntity):
    """基类：把协调器里某个 settings 值映射成可读文本。"""

    _value_key: str = ""
    _name_map: dict[str, str] = {}

    def __init__(self, coordinator: MiMonitorCoordinator, entry: ConfigEntry,
                 description: SensorEntityDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_name = f"{coordinator.display_name} {description.name}"
        self._attr_has_entity_name = False
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        raw = self.coordinator.data.get(self._value_key)
        if raw is None:
            return None
        text = str(raw).strip()
        if not text or text.lower() in ("null", "n/a"):
            return None
        # 已知映射 -> 可读名；未收录（如 HDR 各子模式）原样显示数值
        return self._name_map.get(text, text)


class MiMonitorPictureModeSensor(_CoordinatorValueSensor):
    """当前画面模式（可读名，未收录的模式显示原始编号）。"""

    _value_key = "picture_mode"
    _name_map = _PICTURE_MODE_NAMES


class MiMonitorInputSourceSensor(_CoordinatorValueSensor):
    """当前输入源。"""

    _value_key = "mitv.tvplayer.hdmi.last.source"
    _name_map = _INPUT_SOURCE_NAMES
