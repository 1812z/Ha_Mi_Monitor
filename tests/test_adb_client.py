"""adb_client 的单元测试：验证命令构造与解析逻辑（不依赖真机）。"""

import importlib.util
import os
import sys
import unittest

_MODULE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "custom_components",
    "mi_monitor",
    "adb_client.py",
)

_spec = importlib.util.spec_from_file_location("adb_client_under_test", _MODULE_PATH)
adb_client = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["adb_client_under_test"] = adb_client
_spec.loader.exec_module(adb_client)

AdbClient = adb_client.AdbClient
build_mtk_command = adb_client.build_mtk_command
parse_mtk_batch_output = adb_client.parse_mtk_batch_output
_tvservice_quote = adb_client._tvservice_quote


class TestCommandConstruction(unittest.TestCase):
    def test_serial(self):
        self.assertEqual(AdbClient("192.168.1.10").serial, "192.168.1.10:5555")

    def test_tvservice_quote_uses_ifs(self):
        cmd = _tvservice_quote(["cp", "/a", "/b"])
        self.assertIn('service call TvService 3 s16 "sh -c eval', cmd)
        self.assertIn("\\${IFS}", cmd)
        self.assertNotIn(" cp ", cmd)  # 真实空格必须被 ${IFS} 替代

    def test_mtk_set_command(self):
        cmd = build_mtk_command("set", "g_disp__disp_back_light", 50, 3)
        self.assertIn("MtkDirectTool", cmd)
        self.assertIn("g_disp__disp_back_light", cmd)
        self.assertIn("50", cmd)
        self.assertIn("3", cmd)

    def test_parse_batch_output(self):
        out = (
            "g_disp__disp_back_light=50\n"
            "g_video__clr_temp=1\n"
            "__timestamp=123\n"
            "g_video__broken=ERROR\n"
        )
        parsed = parse_mtk_batch_output(out)
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


if __name__ == "__main__":
    unittest.main()
