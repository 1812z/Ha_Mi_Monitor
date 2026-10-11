"""配置向导：添加一台小米显示器（按 IP）。"""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST

from .adb_client import AdbClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
    }
)


async def _probe(host: str) -> dict[str, object]:
    """尝试连接并取回型号，供向导显示。"""
    client = AdbClient(host)
    try:
        return await client.ping()
    except Exception as exc:  # noqa: BLE001 - 向导里任何异常都要转成可读信息
        _LOGGER.warning("探测 %s 失败: %s", host, exc)
        return {"ok": False, "state": f"error: {exc}", "model": None}


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
            elif host.count(".") != 3 or not all(
                part.isdigit() and 0 <= int(part) <= 255 for part in host.split(".")
            ):
                errors["base"] = "invalid_ip"
            else:
                await self.async_set_unique_id(f"mi_monitor_{host}")
                self._abort_if_unique_id_configured()
                # 测试连接
                result = await _probe(host)
                if not result.get("ok"):
                    errors["base"] = (
                        "cannot_connect" if result.get("state") != "unauthorized"
                        else "unauthorized"
                    )
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
            elif host.count(".") != 3 or not all(
                part.isdigit() and 0 <= int(part) <= 255 for part in host.split(".")
            ):
                errors["base"] = "invalid_ip"
            else:
                result = await _probe(host)
                if not result.get("ok"):
                    errors["base"] = "cannot_connect"
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
