"""集成逻辑验证：不依赖真实 HA，用假 hass 验证「名称取型号」与设备信息装配。

真机样本来自 Redmi G Pro 27U（MiTV-MFFU1），见 test_adb_client.REAL_DEVICE_OUTPUT。
"""

import importlib.util
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_adb_shell import install_fake_adb_shell  # noqa: E402

install_fake_adb_shell()

_BASE = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "custom_components", "mi_monitor"
))


def _load(name):
    spec = importlib.util.spec_from_file_location(
        f"{name}_under_test", os.path.join(_BASE, f"{name}.py")
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[f"{name}_under_test"] = module
    spec.loader.exec_module(module)
    return module


adb_client = _load("adb_client")

# 真机样本
INFO = adb_client.parse_prop_output("\n".join([
    "MiTV-MFFU1", "xiaomi", "xiaomi", "", "beekeeper", "14",
    "OS3.0.109.0.UFFMAMN", "xiaomi/beekeeper/beekeeper:14/x",
    "63895171690000351", "lcd", "3840x2160", "320",
    "xiaomi+MTK9655+beekeeper+OM2+1380lcd27", "tv", "arm64-v8a",
]))
INFO["chipset"] = adb_client.derive_chipset(INFO)
INFO["display_name"] = adb_client.derive_display_name(INFO)


class FakeConfigEntries:
    def __init__(self):
        self.updated = {}

    def async_update_entry(self, entry, **kwargs):
        self.updated.update(kwargs)
        if "title" in kwargs:
            entry.title = kwargs["title"]
        if "data" in kwargs:
            entry.data = kwargs["data"]


class FakeHass:
    def __init__(self):
        self.config_entries = FakeConfigEntries()


class FakeEntry:
    def __init__(self, title, data):
        self.title = title
        self.data = data
        self.entry_id = "test-entry"


def _build_device_info(host, info):
    """复刻 __init__._build_device_info 的逻辑做断言（避免 import HA）。"""
    display = (adb_client.derive_display_name(info)
               or (info.get("model") or "").strip()
               or f"Mi Monitor {host}")
    device = {
        "identifiers": {("mi_monitor", host)},
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


class TestNamingAndInfo(unittest.TestCase):
    def test_entry_title_uses_model(self):
        market = (INFO.get("market_name") or "").strip()
        model = (INFO.get("model") or "").strip()
        title = market or model or "Mi Monitor 1.2.3.4"
        self.assertEqual(title, "MiTV-MFFU1")

    def test_device_info_fields(self):
        device = _build_device_info("192.168.44.100", INFO)
        self.assertEqual(device["name"], "MiTV-MFFU1")          # 名称是型号不是 IP
        self.assertEqual(device["model"], "MiTV-MFFU1")
        self.assertEqual(device["manufacturer"], "Xiaomi")
        self.assertEqual(device["sw_version"], "OS3.0.109.0.UFFMAMN")
        self.assertEqual(device["serial_number"], "63895171690000351")
        self.assertEqual(device["hw_version"], "Android 14")
        self.assertEqual(device["configuration_url"], "http://192.168.44.100")

    def test_device_info_degrades_gracefully(self):
        """属性缺失时不应崩，也不该写入空字段。"""
        device = _build_device_info("10.0.0.9", {})
        self.assertEqual(device["name"], "Mi Monitor 10.0.0.9")
        self.assertNotIn("sw_version", device)
        self.assertNotIn("serial_number", device)

    def test_info_sensor_keys_all_have_source(self):
        """sensor.py 里声明的信息字段都必须来自 DEVICE_PROPERTIES 或已知派生值。"""
        with open(os.path.join(_BASE, "sensor.py"), encoding="utf-8") as handle:
            sensor_src = handle.read()
        known = set(adb_client.DEVICE_PROPERTIES) | {
            "chipset", "display_name", "host", "connection_state",
            "picture_mode_text", "input_source_text",
        }
        # 抓 SensorEntityDescription(key="...") 里的 key
        import re
        keys = set(re.findall(r'key="([a-z_]+)"', sensor_src))
        for key in keys:
            # picture_mode / mitv... 这类是协调器数据键，单独校验
            if key in ("picture_mode", "mitv.tvplayer.hdmi.last.source"):
                continue
            self.assertIn(key, known, f"sensor key {key} 没有数据来源")


class TestTitleMigration(unittest.TestCase):
    """老条目名（Mi Monitor <ip>）应自动升级为型号。"""

    @staticmethod
    def should_rename(title, host, info):
        old_defaults = {f"Mi Monitor {host}", host, ""}
        new_title = (adb_client.derive_display_name(info)
                     or (info.get("model") or "").strip())
        return bool(new_title) and title in old_defaults

    def test_renames_old_default(self):
        self.assertTrue(self.should_rename("Mi Monitor 192.168.44.100", "192.168.44.100", INFO))

    def test_keeps_user_custom_name(self):
        self.assertFalse(self.should_rename("我的显示器", "192.168.44.100", INFO))

    def test_keeps_when_no_info(self):
        self.assertFalse(self.should_rename("Mi Monitor 1.2.3.4", "1.2.3.4", {}))


if __name__ == "__main__":
    unittest.main()
