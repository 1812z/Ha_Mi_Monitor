"""配置向导：添加一台小米显示器（按 IP）。"""

from __future__ import annotations

import asyncio
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .adb_client import AdbClient, resolve_adb_key
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
    }
)


def _valid_ipv4(host: str) -> bool:
    parts = host.split(".")
    if len(parts) != 4:
        return False
    return all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)


async def _probe(hass: HomeAssistant, host: str) -> dict[str, object]:
    """尝试连接并取回型号，供向导显示。

    密钥解析（可能生成 RSA 密钥对）和 ADB 通信都是阻塞操作，全部在线程池里跑，
    不会卡住 Home Assistant 事件循环。
    """
    try:
        key_path = await asyncio.to_thread(resolve_adb_key, hass)
    except Exception as exc:  # noqa: BLE001 - 密钥失败也要给用户可读信息
        _LOGGER.warning("准备 ADB 密钥失败: %s", exc)
        return {"ok": False, "state": f"keyerror: {exc}", "model": None}

    client = AdbClient(host, key_path=key_path)
    try:
        return await client.ping()
    except Exception as exc:  # noqa: BLE001 - 向导里任何异常都要转成可读信息
        _LOGGER.warning("探测 %s 失败: %s", host, exc)
        return {"ok": False, "state": f"error: {exc}", "model": None}


def _error_for(result: dict[str, object]) -> str:
    """把探测结果映射成向导错误 key。"""
    state = str(result.get("state") or "")
    if state == "unauthorized":
        return "unauthorized"
    if state.startswith("keyerror"):
        return "key_error"
    return "cannot_connect"


class MiMonitorConfigFlow(ConfigFlow, domain=DOMAIN):
    """小米显示器 ADB 配置向导。"""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = (user_input.get(CONF_HOST) or "").strip()
            if not host:
                errors["base"] = "no_host"
            elif not _valid_ipv4(host):
                errors["base"] = "invalid_ip"
            else:
                await self.async_set_unique_id(f"mi_monitor_{host}")
                self._abort_if_unique_id_configured()
                # 测试连接
                result = await _probe(self.hass, host)
                if not result.get("ok"):
                    errors["base"] = _error_for(result)
                else:
                    model = result.get("model") or "unknown"
                    return self.async_create_entry(
                        title=f"Mi Monitor {host}",
                        data={CONF_HOST: host},
                        description_placeholders={"model": model},
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """重配已有条目：改 IP 后更新配置（保留原唯一 ID 与实体）。"""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = (user_input.get(CONF_HOST) or "").strip()
            if not host:
                errors["base"] = "no_host"
            elif not _valid_ipv4(host):
                errors["base"] = "invalid_ip"
            else:
                result = await _probe(self.hass, host)
                if not result.get("ok"):
                    errors["base"] = _error_for(result)
                else:
                    entry = self._get_reconfigure_entry()
                    if entry:
                        return self.async_update_entry(
                            entry,
                            data={**entry.data, CONF_HOST: host},
                        )
                    return self.async_abort(reason="reconfigure_failed")

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )
