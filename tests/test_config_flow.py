"""config_flow 的轻量测试：IP 校验与唯一 ID 逻辑（不依赖真实 HA 运行时）。"""

import os
import re
import sys
import unittest

# 读取 config_flow.py 源码做静态断言（不 import，避免 homeassistant 依赖）
_CFG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "custom_components",
    "mi_monitor",
    "config_flow.py",
)
with open(_CFG_PATH, encoding="utf-8") as _f:
    _SRC = _f.read()

_IP_PATTERN = re.compile(
    r"host\.count\(\"\.\"\) != 3 or not all\("
)


class TestConfigFlowShape(unittest.TestCase):
    def test_has_flow_class(self):
        self.assertIn("class MiMonitorConfigFlow(ConfigFlow", _SRC)
        self.assertIn("domain=DOMAIN", _SRC)

    def test_ip_validation_present(self):
        # 校验 IPv4 格式（4 段、0-255）
        self.assertIn('host.count(".") != 3', _SRC)
        self.assertIn("part.isdigit() and 0 <= int(part) <= 255", _SRC)

    def test_unique_id_is_ip_based(self):
        # 一台显示器 = 一个唯一 ID，防重复添加
        self.assertIn('async_set_unique_id(f"mi_monitor_{host}")', _SRC)
        self.assertIn("_abort_if_unique_id_configured", _SRC)

    def test_connection_probe_called(self):
        self.assertIn("await _probe(host)", _SRC)

    def test_reconfigure_present(self):
        self.assertIn("async_step_reconfigure", _SRC)
        self.assertIn("async_update_entry", _SRC)


if __name__ == "__main__":
    unittest.main()
