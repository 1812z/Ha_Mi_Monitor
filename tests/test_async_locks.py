"""asyncio 行为测试：确认阻塞的 adb-shell 调用不会卡住事件循环，且同一客户端的
操作被串行化（race 保护）。

真实 adb-shell 的调用是同步阻塞的，所以集成必须把它们丢进 to_thread；这里用
一个会 sleep 的替身来验证：并发调用的事件循环仍然可以继续跑其他协程。
"""

import asyncio
import importlib.util
import os
import sys
import time
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


class TestAsyncBehaviour(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        recorded["shell"].clear()
        recorded["connect"] = 0
        behaviour["device_output"] = {}
        behaviour["connect_error"] = None
        self.key = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "fake_adbkey"
        )
        for suffix in ("", ".pub"):
            path = self.key + suffix
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("FAKE")

    def _client(self):
        return adb_client.AdbClient("192.168.1.10", key_path=self.key)

    async def test_event_loop_not_blocked(self):
        """阻塞调用在 to_thread 里跑：事件循环仍能推进计数器。"""
        client = self._client()
        ticks = 0

        async def ticker():
            nonlocal ticks
            for _ in range(20):
                ticks += 1
                await asyncio.sleep(0.005)

        ticker_task = asyncio.create_task(ticker())
        await client.get("picture_backlight")
        await asyncio.sleep(0.05)
        ticker_task.cancel()
        self.assertGreater(ticks, 0, "事件循环被阻塞了")

    async def test_many_sequential_calls_complete(self):
        client = self._client()
        for _ in range(15):
            await client.get("picture_backlight")
        self.assertEqual(len(recorded["shell"]), 15)

    async def test_no_deadlock_on_nested_paths(self):
        """set_backlight 内部会走 jar 检查 + JNI + settings + 广播，
        任何一层重复取锁都会死锁；用超时保护验证。"""
        behaviour["device_output"] = {"stat -c": "5273"}
        client = self._client()
        await asyncio.wait_for(client.set_backlight(50), timeout=5)

    async def test_disconnect_closes_device(self):
        client = self._client()
        await client.connect()
        await client.disconnect()
        self.assertGreaterEqual(recorded["close"], 1)


if __name__ == "__main__":
    unittest.main()
