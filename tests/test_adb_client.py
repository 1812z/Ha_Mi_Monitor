"""adb_client 测试：用 adb-shell 替身验证命令序列、事务与容错。

真实 adb-shell 由 HA 依据 manifest.json 的 requirements 自动安装；这里用签名
完全对齐的替身来验证集成的调用是否正确、串行锁是否生效、异常是否归类正确。
"""

import asyncio
import importlib.util
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_adb_shell import install_fake_adb_shell  # noqa: E402

recorded, behaviour, exceptions_mod = install_fake_adb_shell()

_BASE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "custom_components",
    "mi_monitor",
)
_spec = importlib.util.spec_from_file_location(
    "adb_client_under_test", os.path.join(_BASE, "adb_client.py")
)
adb_client = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["adb_client_under_test"] = adb_client
_spec.loader.exec_module(adb_client)

AdbClient = adb_client.AdbClient
AdbError = adb_client.AdbError


def _reset():
    recorded["shell"].clear()
    recorded["push"].clear()
    recorded["connect"] = 0
    recorded["close"] = 0
    behaviour["device_output"] = {}
    behaviour["connect_error"] = None
    behaviour["shell_error"] = None
    behaviour["push_error"] = None


class TestCommandConstruction(unittest.TestCase):
    def test_serial(self):
        self.assertEqual(AdbClient("192.168.1.10").serial, "192.168.1.10:5555")

    def test_tvservice_quote_uses_ifs(self):
        cmd = adb_client._tvservice_quote(["cp", "/a", "/b"])
        self.assertIn('service call TvService 3 s16 "sh -c eval', cmd)
        self.assertIn("\\${IFS}", cmd)
        self.assertNotIn(" cp ", cmd)  # 真实空格必须写成 ${IFS}

    def test_mtk_set_command(self):
        cmd = adb_client.build_mtk_command("set", "g_disp__disp_back_light", 50, 3)
        self.assertIn("MtkDirectTool", cmd)
        self.assertIn("g_disp__disp_back_light", cmd)
        self.assertIn("50", cmd)

    def test_parse_batch_output(self):
        out = (
            "g_disp__disp_back_light=50\n"
            "g_video__clr_temp=1\n"
            "__timestamp=123\n"
            "g_video__broken=ERROR\n"
        )
        parsed = adb_client.parse_mtk_batch_output(out)
        self.assertEqual(parsed["g_disp__disp_back_light"], "50")
        self.assertEqual(parsed["g_video__clr_temp"], "1")
        self.assertNotIn("__timestamp", parsed)
        self.assertNotIn("g_video__broken", parsed)

    def test_input_source_mapping(self):
        client = AdbClient("10.0.0.5")
        self.assertEqual(client.INPUT_SOURCES["hdmi1"], 23)
        self.assertEqual(client.INPUT_SOURCES["hdmi2"], 24)
        self.assertEqual(client.INPUT_SOURCES["dp"], 29)
        self.assertEqual(client.INPUT_SOURCES["usbc"], 30)

    def test_available_is_property_not_method(self):
        """回归测试：真实 adb-shell 的 available 是 property，调用会 TypeError。"""
        with open(adb_client.__file__, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn(".available()", source)


REAL_DEVICE_OUTPUT = "\n".join([
    "MiTV-MFFU1",                    # model
    "xiaomi",                        # brand
    "xiaomi",                        # manufacturer
    "",                              # market_name（该机型为空）
    "beekeeper",                     # product
    "14",                            # android_version
    "OS3.0.109.0.UFFMAMN",           # firmware
    "xiaomi/beekeeper/beekeeper:14/x",  # fingerprint
    "63895171690000351",             # serial
    "lcd",                           # panel_type
    "3840x2160",                     # display_size
    "320",                           # density
    "xiaomi+MTK9655+beekeeper+OM2+1380lcd27",  # device_unique
    "tv",                            # characteristics
    "arm64-v8a",                     # cpu_abi
])


class TestDeviceInfoParsing(unittest.TestCase):
    """设备信息解析（真机输出样本）。"""

    def test_parse_real_output(self):
        info = adb_client.parse_prop_output(REAL_DEVICE_OUTPUT)
        self.assertEqual(info["model"], "MiTV-MFFU1")
        self.assertEqual(info["manufacturer"], "xiaomi")
        self.assertEqual(info["firmware"], "OS3.0.109.0.UFFMAMN")
        self.assertEqual(info["display_size"], "3840x2160")
        self.assertEqual(info["panel_type"], "lcd")
        self.assertEqual(info["serial"], "63895171690000351")
        # 空属性应被丢弃，而不是留下空串
        self.assertNotIn("market_name", info)

    def test_chipset_from_unique(self):
        info = adb_client.parse_prop_output(REAL_DEVICE_OUTPUT)
        self.assertEqual(adb_client.derive_chipset(info), "MTK9655")

    def test_chipset_empty_when_unknown(self):
        self.assertEqual(adb_client.derive_chipset({}), "")
        self.assertEqual(
            adb_client.derive_chipset({"device_unique": "xiaomi+beekeeper+OM2"}),
            "",
        )

    def test_display_name_prefers_market_name(self):
        self.assertEqual(
            adb_client.derive_display_name({"market_name": "红米 G Pro 27U"}),
            "红米 G Pro 27U",
        )

    def test_display_name_falls_back_to_model(self):
        info = adb_client.parse_prop_output(REAL_DEVICE_OUTPUT)
        self.assertEqual(adb_client.derive_display_name(info), "MiTV-MFFU1")

    def test_display_name_falls_back_to_product(self):
        self.assertEqual(
            adb_client.derive_display_name({"product": "beekeeper"}), "beekeeper"
        )

    def test_display_name_empty_when_nothing(self):
        self.assertEqual(adb_client.derive_display_name({}), "")

    def test_parse_handles_short_output(self):
        """设备只回了部分行时不能崩，也不能错位。"""
        info = adb_client.parse_prop_output("MiTV-MFFU1\nxiaomi\n")
        self.assertEqual(info["model"], "MiTV-MFFU1")
        self.assertEqual(info["brand"], "xiaomi")
        self.assertNotIn("firmware", info)

    def test_parse_drops_null_and_unknown(self):
        out = "null\nunknown\nxiaomi\n"
        info = adb_client.parse_prop_output(out)
        self.assertNotIn("model", info)
        self.assertNotIn("brand", info)
        self.assertEqual(info["manufacturer"], "xiaomi")


class TestRuntimeBehaviour(unittest.IsolatedAsyncioTestCase):
    """用替身跑真实的异步调用路径。"""

    def setUp(self):
        _reset()
        self.key = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "fake_adbkey"
        )
        for suffix in ("", ".pub"):
            path = self.key + suffix
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("FAKE")

    def _client(self):
        return AdbClient("192.168.1.10", key_path=self.key)

    async def test_connect_ok(self):
        client = self._client()
        ok, state = await client.connect()
        self.assertTrue(ok)
        self.assertEqual(state, "device")

    async def test_unauthorized_maps_to_state(self):
        behaviour["connect_error"] = exceptions_mod.TcpTimeoutException("boom")
        client = self._client()
        ok, state = await client.connect()
        self.assertFalse(ok)
        self.assertEqual(state, "unauthorized")

    async def test_get_uses_settings_get(self):
        behaviour["device_output"] = {"settings get global picture_backlight": "42"}
        client = self._client()
        self.assertEqual(await client.get("picture_backlight"), "42")
        self.assertIn("settings get global picture_backlight",
                      recorded["shell"][0]["command"])

    async def test_set_backlight_full_sequence(self):
        behaviour["device_output"] = {"stat -c": "5273"}
        client = self._client()
        await client.set_backlight(41)
        joined = "\n".join(c["command"] for c in recorded["shell"])
        self.assertIn("g_disp__disp_back_light", joined)          # JNI 直写
        self.assertIn("settings put global picture_backlight 41", joined)
        self.assertIn("settings put global xiaomi_picture_backlight 41", joined)
        self.assertIn("PIC_MODE_CHANGED", joined)                  # 刷新广播

    async def test_set_backlight_clamps_range(self):
        behaviour["device_output"] = {"stat -c": "5273"}
        client = self._client()
        await client.set_backlight(999)
        joined = "\n".join(c["command"] for c in recorded["shell"])
        self.assertIn("settings put global picture_backlight 100", joined)

    async def test_jar_heal_caches(self):
        behaviour["device_output"] = {"stat -c": "5273"}
        client = self._client()
        self.assertTrue(await client.check_and_heal_jar())
        self.assertTrue(client._jar_healed)
        before = len(recorded["shell"])
        self.assertTrue(await client.check_and_heal_jar())
        self.assertEqual(len(recorded["shell"]), before, "缓存命中后不应再发命令")

    async def test_jar_missing_pushes(self):
        behaviour["device_output"] = {"stat -c": "0"}  # 设备上没有 jar
        client = self._client()
        ok = await client.check_and_heal_jar(local_jar=adb_client.locate_mtk_jar())
        self.assertTrue(ok)
        self.assertTrue(recorded["push"], "应把 jar 推到设备")

    async def test_input_source_switch(self):
        client = self._client()
        await client.set_input_source("HDMI 1")
        joined = "\n".join(c["command"] for c in recorded["shell"])
        self.assertIn("am force-stop com.xiaomi.mitv.tvplayer", joined)
        self.assertIn("EXTSRC_PLAY", joined)
        self.assertIn("--ei input 23", joined)
        self.assertIn("settings put global mitv.tvplayer.hdmi.last.source 23", joined)

    async def test_input_source_accepts_english_key(self):
        client = self._client()
        await client.set_input_source("usbc")
        joined = "\n".join(c["command"] for c in recorded["shell"])
        self.assertIn("--ei input 30", joined)

    async def test_unknown_input_source_raises(self):
        client = self._client()
        with self.assertRaises(ValueError):
            await client.set_input_source("nope")

    async def test_picture_mode_both_naming_schemes(self):
        client = self._client()
        await client.set_picture_mode("电影")
        joined = "\n".join(c["command"] for c in recorded["shell"])
        self.assertIn("settings put global picture_mode 9", joined)
        _reset()
        await client.set_picture_mode("game")
        joined = "\n".join(c["command"] for c in recorded["shell"])
        self.assertIn("settings put global picture_mode 10", joined)

    async def test_picture_mode_unknown_raises(self):
        client = self._client()
        with self.assertRaises(ValueError):
            await client.set_picture_mode("bogus")

    async def test_get_input_source_maps_back(self):
        behaviour["device_output"] = {
            "settings get global mitv.tvplayer.hdmi.last.source": "24"
        }
        client = self._client()
        self.assertEqual(await client.get_input_source(), "hdmi2")

    async def test_shell_timeout_becomes_adb_error(self):
        behaviour["shell_error"] = exceptions_mod.AdbTimeoutError("too slow")
        client = self._client()
        with self.assertRaises(AdbError):
            await client.get("picture_backlight")

    async def test_concurrent_calls_serialized(self):
        """并发调用不能交错：连接只应建立一次。"""
        behaviour["device_output"] = {"settings get global picture_backlight": "42"}
        client = self._client()
        await asyncio.gather(
            client.get("picture_backlight"),
            client.get("picture_contrast"),
            client.get("picture_hue"),
        )
        self.assertEqual(recorded["connect"], 1)

    async def test_get_device_info_single_shell_call(self):
        """设备信息应一次 shell 读完（而不是逐条 getprop）。"""
        behaviour["device_output"] = {"getprop": REAL_DEVICE_OUTPUT}
        client = self._client()
        info = await client.get_device_info()
        self.assertEqual(info["model"], "MiTV-MFFU1")
        self.assertEqual(info["chipset"], "MTK9655")
        self.assertEqual(info["display_name"], "MiTV-MFFU1")
        self.assertEqual(len(recorded["shell"]), 1)

    async def test_ping_returns_info(self):
        behaviour["device_output"] = {"getprop": REAL_DEVICE_OUTPUT}
        client = self._client()
        result = await client.ping()
        self.assertTrue(result["ok"])
        self.assertEqual(result["model"], "MiTV-MFFU1")
        self.assertEqual(result["info"]["firmware"], "OS3.0.109.0.UFFMAMN")

    async def test_ping_failure_has_empty_info(self):
        behaviour["connect_error"] = exceptions_mod.TcpTimeoutException("nope")
        client = self._client()
        result = await client.ping()
        self.assertFalse(result["ok"])
        self.assertEqual(result["info"], {})


if __name__ == "__main__":
    unittest.main()
