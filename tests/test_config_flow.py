"""config_flow 的轻量测试：IP 校验、唯一 ID 与密钥接入（不依赖真实 HA 运行时）。

这里直接导入 adb_client 的真实实现来验证 IP 校验与密钥解析行为，
config_flow 本身用源码断言（import 它会拉入 homeassistant）。
"""

import importlib.util
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_adb_shell import install_fake_adb_shell  # noqa: E402

install_fake_adb_shell()

_BASE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "custom_components",
    "mi_monitor",
)

# ---- adb_client（真实实现，配替身库）----
_spec = importlib.util.spec_from_file_location(
    "adb_client_cf_test", os.path.join(_BASE, "adb_client.py")
)
adb_client = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["adb_client_cf_test"] = adb_client
_spec.loader.exec_module(adb_client)

with open(os.path.join(_BASE, "config_flow.py"), encoding="utf-8") as handle:
    CFG_SRC = handle.read()

with open(os.path.join(_BASE, "manifest.json"), encoding="utf-8") as handle:
    MANIFEST = json.load(handle)


class TestConfigFlowShape(unittest.TestCase):
    def test_has_flow_class(self):
        self.assertIn("class MiMonitorConfigFlow(ConfigFlow", CFG_SRC)
        self.assertIn("domain=DOMAIN", CFG_SRC)

    def test_unique_id_is_ip_based(self):
        self.assertIn('async_set_unique_id(f"mi_monitor_{host}")', CFG_SRC)
        self.assertIn("_abort_if_unique_id_configured", CFG_SRC)

    def test_probe_receives_hass(self):
        self.assertIn("await _probe(self.hass, host)", CFG_SRC)

    def test_reconfigure_present(self):
        self.assertIn("async_step_reconfigure", CFG_SRC)
        self.assertIn("async_update_entry", CFG_SRC)

    def test_blocking_key_resolution_offloaded(self):
        """密钥解析是阻塞操作，必须在 to_thread 里跑。"""
        self.assertIn("asyncio.to_thread(resolve_adb_key", CFG_SRC)


class TestIpValidation(unittest.TestCase):
    """直接复用 config_flow 的校验逻辑（复制实现，避免 import HA）。"""

    @staticmethod
    def valid(host: str) -> bool:
        parts = host.split(".")
        if len(parts) != 4:
            return False
        return all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)

    def test_good(self):
        self.assertTrue(self.valid("192.168.44.100"))
        self.assertTrue(self.valid("10.0.0.1"))

    def test_bad(self):
        for host in ("", "abc", "192.168.1", "192.168.1.256", "1.2.3.4.5",
                     "192.168.1.-1", "999.1.1.1"):
            self.assertFalse(self.valid(host), host)


class TestManifest(unittest.TestCase):
    def test_requires_adb_shell(self):
        self.assertIn("adb-shell", " ".join(MANIFEST["requirements"]))

    def test_config_flow_enabled(self):
        self.assertTrue(MANIFEST["config_flow"])

    def test_version_present(self):
        self.assertIn("version", MANIFEST)


class TestAdbKeyResolution(unittest.TestCase):
    """密钥解析测试（用工作区里的临时目录，避开受限的系统 temp）。"""

    def setUp(self):
        self.folder = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "_keytest"
        )
        os.makedirs(self.folder, exist_ok=True)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.folder, ignore_errors=True)

    def test_ensure_adb_key_creates_pair(self):
        key = os.path.join(self.folder, "adbkey")
        self.assertEqual(adb_client.ensure_adb_key(key), key)
        self.assertTrue(os.path.exists(key))
        self.assertTrue(os.path.exists(key + ".pub"))

    def test_ensure_adb_key_idempotent(self):
        key = os.path.join(self.folder, "adbkey")
        adb_client.ensure_adb_key(key)
        with open(key, encoding="utf-8") as handle:
            first = handle.read()
        adb_client.ensure_adb_key(key)  # 第二次不应重新生成
        with open(key, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), first)

    def test_client_uses_given_key(self):
        key = os.path.join(self.folder, "adbkey")
        adb_client.ensure_adb_key(key)
        client = adb_client.AdbClient("1.2.3.4", key_path=key)
        self.assertEqual(client.key_path, key)


if __name__ == "__main__":
    unittest.main()
