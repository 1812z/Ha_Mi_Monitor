"""asyncio 层测试：验证 set_backlight / check_and_heal_jar 无 semaphore 死锁。

用 monkeypatch 的假 adb 输出替代真实子进程（沙箱限制 asyncio 管道）。
"""

import asyncio
import os
import sys
import unittest
import importlib.util

_base = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "custom_components",
    "mi_monitor",
)
_spec = importlib.util.spec_from_file_location(
    "adb_client_under_test", os.path.join(_base, "adb_client.py")
)
adb_client = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["adb_client_under_test"] = adb_client
_spec.loader.exec_module(adb_client)


def _make_fake_run(client):
    """monkeypatch client._run 以模拟真实设备输出（不起真实子进程）。"""
    calls = []

    async def fake_run(args, timeout=10):
        calls.append(list(args))
        joined = " ".join(args)
        if "get-state" in joined:
            return 0, "device", ""
        if "connect" in joined:
            return 0, f"connected to {args[-1]}", ""
        if "shell" in joined:
            body = joined[joined.index("shell") + 1:]
            if "settings get global picture_backlight" in body:
                return 0, "42", ""
            if "stat -c" in body:
                return 0, "5273", ""
            if "am broadcast" in body:
                return 0, "Broadcast completed", ""
            return 0, "", ""
        return 0, "", ""

    client._run = fake_run
    return calls


class TestAsyncLocks(unittest.IsolatedAsyncioTestCase):
    async def test_jar_heal_and_cache(self):
        client = adb_client.AdbClient("192.168.1.10")
        calls = _make_fake_run(client)

        ok = await client.check_and_heal_jar(local_jar="/tmp/MtkDirectTool.jar")
        self.assertTrue(ok)
        self.assertTrue(client._jar_healed)

        calls.clear()
        ok = await client.check_and_heal_jar()
        self.assertTrue(ok)
        self.assertEqual(calls, [], "缓存命中后不应再有调用")

    async def test_set_backlight_no_deadlock(self):
        client = adb_client.AdbClient("192.168.1.10")
        calls = _make_fake_run(client)

        # 若 set_backlight 里 _heal_jar_locked 再拿锁会死锁，wait_for 会超时
        await asyncio.wait_for(client.set_backlight(55), timeout=5)
        put_calls = [
            c for c in calls if "settings put global picture_backlight" in " ".join(c)
        ]
        self.assertTrue(put_calls, "应写入 picture_backlight")


if __name__ == "__main__":
    unittest.main()

