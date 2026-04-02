"""Config flow for Termogea."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TermogeaAuthError, TermogeaClient
from .const import (
    CONF_REQUEST_TIMEOUT,
    CONF_SCAN_INTERVAL,
    CONF_ZONE_MAP_PATH,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_ZONE_MAP_PATH,
    DOMAIN,
    GLOBAL_MODES,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    SEASON_MODES,
    SEASON_MODE_SUMMER,
    SEASON_MODE_WINTER,
    WEEKDAY_OPTIONS,
)
from .models import GlobalConfig, RegisterDefinition, ScheduleRule, ZoneDefinition
from .storage_manager import TermogeaStorageManager
from .zone_map import ZoneMapError, load_zone_map


def _sanitize_connection_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Normalize connection values entered from forms."""
    return {
        **user_input,
        CONF_HOST: str(user_input[CONF_HOST]).strip(),
        CONF_USERNAME: str(user_input[CONF_USERNAME]).strip(),
        CONF_PASSWORD: str(user_input[CONF_PASSWORD]).strip(),
        CONF_ZONE_MAP_PATH: str(user_input.get(CONF_ZONE_MAP_PATH, DEFAULT_ZONE_MAP_PATH)).strip(),
        CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
        CONF_REQUEST_TIMEOUT: int(user_input[CONF_REQUEST_TIMEOUT]),
    }


def _connection_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "192.168.0.52")): str,
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "admin")): str,
            vol.Required(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, "admin")): str,
            vol.Optional(
                CONF_ZONE_MAP_PATH,
                default=defaults.get(CONF_ZONE_MAP_PATH, DEFAULT_ZONE_MAP_PATH),
            ): str,
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)),
            vol.Optional(
                CONF_REQUEST_TIMEOUT,
                default=defaults.get(CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT),
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=60)),
        }
    )


def _selector_options(values: list[str]) -> list[selector.SelectOptionDict]:
    return [{"value": value, "label": value} for value in values]


def _zone_selector_options(zones: list[ZoneDefinition]) -> list[selector.SelectOptionDict]:
    """Build readable selector options for zones."""
    ordered = sorted(zones, key=lambda zone: (zone.name or zone.zone_id).lower())
    options: list[selector.SelectOptionDict] = []
    for zone in ordered:
        label = (zone.name or zone.zone_id).strip() or zone.zone_id
        if label.lower() != zone.zone_id.lower():
            label = f"{label} ({zone.zone_id})"
        options.append({"value": zone.zone_id, "label": label})
    return options


def _global_schema(defaults: GlobalConfig) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required("global_enabled", default=defaults.global_enabled): bool,
            vol.Required("automations_enabled", default=defaults.automations_enabled): bool,
            vol.Required(
                "allow_common_without_people",
                default=defaults.allow_common_without_people,
            ): bool,
            vol.Required(
                "global_mode",
                default=defaults.global_mode,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_selector_options(GLOBAL_MODES),
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                "auto_fallback_mode",
                default=defaults.auto_fallback_mode,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_selector_options(GLOBAL_MODES),
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                "season_mode",
                default=defaults.season_mode,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_selector_options(SEASON_MODES),
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required("schedule_enabled", default=defaults.schedule_enabled): bool,
            vol.Required(
                "winter_comfort_temp",
                default=defaults.winter_comfort_temp,
            ): vol.Coerce(float),
            vol.Required("winter_eco_temp", default=defaults.winter_eco_temp): vol.Coerce(float),
            vol.Required("winter_away_temp", default=defaults.winter_away_temp): vol.Coerce(float),
            vol.Required("winter_night_temp", default=defaults.winter_night_temp): vol.Coerce(float),
            vol.Required(
                "winter_inactive_temp",
                default=defaults.winter_inactive_temp,
            ): vol.Coerce(float),
            vol.Required(
                "summer_comfort_temp",
                default=defaults.summer_comfort_temp,
            ): vol.Coerce(float),
            vol.Required("summer_eco_temp", default=defaults.summer_eco_temp): vol.Coerce(float),
            vol.Required("summer_away_temp", default=defaults.summer_away_temp): vol.Coerce(float),
            vol.Required("summer_night_temp", default=defaults.summer_night_temp): vol.Coerce(float),
            vol.Required(
                "summer_inactive_temp",
                default=defaults.summer_inactive_temp,
            ): vol.Coerce(float),
        }
    )


def _register_to_defaults(register: RegisterDefinition | None) -> dict[str, Any]:
    if register is None:
        return {}
    return {
        "mod": register.mod,
        "reg": register.reg,
        "scale": register.scale,
        "precision": register.precision,
        "min_value": register.min_value,
        "max_value": register.max_value,
        "step": register.step,
        "off_value": register.off_value,
        "heat_value": register.heat_value,
    }


def _build_register(prefix: str, data: dict[str, Any]) -> RegisterDefinition | None:
    mod = data.get(f"{prefix}_mod")
    reg = data.get(f"{prefix}_reg")
    if mod in (None, "") or reg in (None, ""):
        return None
    return RegisterDefinition(
        mod=int(mod),
        reg=int(reg),
        scale=float(data.get(f"{prefix}_scale", 1.0) or 1.0),
        precision=int(data.get(f"{prefix}_precision", 1) or 1),
        min_value=(float(data[f"{prefix}_min_value"]) if data.get(f"{prefix}_min_value") not in (None, "") else None),
        max_value=(float(data[f"{prefix}_max_value"]) if data.get(f"{prefix}_max_value") not in (None, "") else None),
        step=(float(data[f"{prefix}_step"]) if data.get(f"{prefix}_step") not in (None, "") else None),
        off_value=(int(data[f"{prefix}_off_value"]) if data.get(f"{prefix}_off_value") not in (None, "") else None),
        heat_value=(int(data[f"{prefix}_heat_value"]) if data.get(f"{prefix}_heat_value") not in (None, "") else None),
    )


def _zone_policy_schema(hass, defaults: ZoneDefinition | None = None) -> vol.Schema:
    defaults = defaults or ZoneDefinition(zone_id="", name="")
    schema: dict[vol.Marker, object] = {
        vol.Required("zone_id", default=defaults.zone_id): str,
        vol.Required("name", default=defaults.name): str,
        vol.Required("enabled", default=defaults.enabled): bool,
        vol.Required(
            "manual_override_allowed",
            default=defaults.manual_override_allowed,
        ): bool,
        vol.Required(
            "custom_setpoints",
            default=defaults.custom_setpoints,
        ): bool,
        vol.Required(
            "custom_schedule",
            default=defaults.custom_schedule,
        ): bool,
        vol.Required(
            "schedule_enabled",
            default=defaults.schedule_enabled,
        ): bool,
        vol.Required("is_common_area", default=defaults.is_common_area): bool,
        vol.Optional("people", default=defaults.people): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain="person",
                multiple=True,
            )
        ),
        vol.Required("comfort_temp", default=defaults.comfort_temp): vol.Coerce(float),
        vol.Required("eco_temp", default=defaults.eco_temp): vol.Coerce(float),
        vol.Required("away_temp", default=defaults.away_temp): vol.Coerce(float),
        vol.Required("night_temp", default=defaults.night_temp): vol.Coerce(float),
        vol.Required("inactive_temp", default=defaults.inactive_temp): vol.Coerce(float),
    }

    if defaults.presence_sensor:
        presence_key: vol.Marker = vol.Optional(
            "presence_sensor",
            default=defaults.presence_sensor,
        )
    else:
        presence_key = vol.Optional("presence_sensor")

    schema[presence_key] = selector.EntitySelector(
        selector.EntitySelectorConfig(
            domain="binary_sensor",
            multiple=False,
        )
    )

    return vol.Schema(schema)


def _zone_mapping_schema(defaults: ZoneDefinition | None = None) -> vol.Schema:
    defaults = defaults or ZoneDefinition(zone_id="", name="")
    current = _register_to_defaults(defaults.current_temperature)
    humidity = _register_to_defaults(defaults.current_humidity)
    target = _register_to_defaults(defaults.target_temperature)
    hvac = _register_to_defaults(defaults.hvac_mode)
    return vol.Schema(
        {
            vol.Optional("current_mod", default=current.get("mod", "")): str,
            vol.Optional("current_reg", default=current.get("reg", "")): str,
            vol.Optional("current_scale", default=current.get("scale", 1.0)): vol.Coerce(float),
            vol.Optional("current_precision", default=current.get("precision", 1)): vol.Coerce(int),
            vol.Optional("humidity_mod", default=humidity.get("mod", "")): str,
            vol.Optional("humidity_reg", default=humidity.get("reg", "")): str,
            vol.Optional("humidity_scale", default=humidity.get("scale", 1.0)): vol.Coerce(float),
            vol.Optional("humidity_precision", default=humidity.get("precision", 1)): vol.Coerce(int),
            vol.Optional("target_mod", default=target.get("mod", "")): str,
            vol.Optional("target_reg", default=target.get("reg", "")): str,
            vol.Optional("target_scale", default=target.get("scale", 1.0)): vol.Coerce(float),
            vol.Optional("target_precision", default=target.get("precision", 1)): vol.Coerce(int),
            vol.Optional("target_min_value", default=target.get("min_value", "")): str,
            vol.Optional("target_max_value", default=target.get("max_value", "")): str,
            vol.Optional("target_step", default=target.get("step", "")): str,
            vol.Optional("hvac_mod", default=hvac.get("mod", "")): str,
            vol.Optional("hvac_reg", default=hvac.get("reg", "")): str,
            vol.Optional("hvac_off_value", default=hvac.get("off_value", "")): str,
            vol.Optional("hvac_heat_value", default=hvac.get("heat_value", "")): str,
        }
    )


def _schedule_schema(defaults: ScheduleRule | None = None) -> vol.Schema:
    defaults = defaults or ScheduleRule(rule_id="", name="", days=["mon"], start="08:00", end="18:00", mode="comfort")
    return vol.Schema(
        {
            vol.Required("rule_id", default=defaults.rule_id): str,
            vol.Required("name", default=defaults.name): str,
            vol.Required(
                "days",
                default=defaults.days,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_selector_options(WEEKDAY_OPTIONS),
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required("start", default=defaults.start): str,
            vol.Required("end", default=defaults.end): str,
            vol.Required(
                "mode",
                default=defaults.mode,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_selector_options(GLOBAL_MODES),
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


class TermogeaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Termogea."""

    VERSION = 3

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            cleaned = _sanitize_connection_input(user_input)
            await self.async_set_unique_id(cleaned[CONF_HOST].lower())
            self._abort_if_unique_id_configured()

            try:
                client = TermogeaClient(
                    async_get_clientsession(self.hass),
                    cleaned[CONF_HOST],
                    cleaned[CONF_USERNAME],
                    cleaned[CONF_PASSWORD],
                    cleaned[CONF_REQUEST_TIMEOUT],
                )
                await client.async_login()
                await client.async_check_thcontrol_status()
            except TermogeaAuthError:
                errors["base"] = "invalid_auth"
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                try:
                    controller_name = await client.async_fetch_controller_name()
                except Exception:
                    controller_name = None
                entry = self.async_create_entry(
                    title=controller_name or f"Termogea {cleaned[CONF_HOST]}",
                    data=cleaned,
                )
                return entry

        return self.async_show_form(
            step_id="user",
            data_schema=_connection_schema(user_input),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return TermogeaOptionsFlow()


class TermogeaOptionsFlow(config_entries.OptionsFlow):
    """Handle Termogea options and persistent config."""

    def __init__(self) -> None:
        super().__init__()
        self._storage: TermogeaStorageManager | None = None
        self._editing_zone_id: str | None = None
        self._editing_schedule_id: str | None = None
        self._editing_schedule_season: str = SEASON_MODE_WINTER
        self._editing_zone_schedule_zone_id: str | None = None
        self._editing_zone_schedule_id: str | None = None

    async def _async_storage(self) -> TermogeaStorageManager:
        if self._storage is None:
            self._storage = TermogeaStorageManager(self.hass, self.config_entry.entry_id)
            await self._storage.async_load()
        return self._storage

    async def _async_finish_and_reload(self, data: dict[str, Any] | None = None) -> FlowResult:
        self.hass.async_create_task(self.hass.config_entries.async_reload(self.config_entry.entry_id))
        merged_options = dict(self.config_entry.options)
        if data:
            merged_options.update(data)
        return self.async_create_entry(title="", data=merged_options)

    async def async_step_init(self, _user_input: dict[str, Any] | None = None) -> FlowResult:
        await self._async_storage()
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "connection",
                "global_settings",
                "season_settings",
                "add_zone",
                "edit_zone_select",
                "edit_zone_mapping_select",
                "delete_zone_select",
                "add_schedule_winter",
                "edit_schedule_select_winter",
                "delete_schedule_select_winter",
                "add_schedule_summer",
                "edit_schedule_select_summer",
                "delete_schedule_select_summer",
                "add_zone_schedule_winter",
                "edit_zone_schedule_select_winter",
                "delete_zone_schedule_select_winter",
                "add_zone_schedule_summer",
                "edit_zone_schedule_select_summer",
                "delete_zone_schedule_select_summer",
                "import_legacy_yaml",
            ],
        )

    async def async_step_connection(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        defaults = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            cleaned = _sanitize_connection_input(user_input)
            try:
                client = TermogeaClient(
                    async_get_clientsession(self.hass),
                    cleaned[CONF_HOST],
                    cleaned[CONF_USERNAME],
                    cleaned[CONF_PASSWORD],
                    cleaned[CONF_REQUEST_TIMEOUT],
                )
                await client.async_login()
                await client.async_check_thcontrol_status()
            except TermogeaAuthError:
                errors["base"] = "invalid_auth"
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                try:
                    controller_name = await client.async_fetch_controller_name()
                except Exception:
                    controller_name = None
                preserved_options = dict(self.config_entry.options)
                for key in (
                    CONF_HOST,
                    CONF_USERNAME,
                    CONF_PASSWORD,
                    CONF_SCAN_INTERVAL,
                    CONF_REQUEST_TIMEOUT,
                    CONF_ZONE_MAP_PATH,
                ):
                    preserved_options.pop(key, None)

                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    title=controller_name or self.config_entry.title,
                    data={**self.config_entry.data, **cleaned},
                    options=preserved_options,
                )
                return await self._async_finish_and_reload()

        return self.async_show_form(
            step_id="connection",
            data_schema=_connection_schema(defaults),
            errors=errors,
        )

    async def async_step_global_settings(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        storage = await self._async_storage()
        if user_input is not None:
            current = storage.config.global_config
            updated = GlobalConfig(
                global_enabled=bool(user_input["global_enabled"]),
                automations_enabled=bool(user_input["automations_enabled"]),
                allow_common_without_people=bool(user_input["allow_common_without_people"]),
                season_mode=str(user_input["season_mode"]).lower(),
                global_mode=str(user_input["global_mode"]),
                auto_fallback_mode=str(user_input["auto_fallback_mode"]),
                # Keep legacy non-seasonal fields aligned with winter values
                # for backward compatibility with existing policy math.
                comfort_temp=float(user_input["winter_comfort_temp"]),
                eco_temp=float(user_input["winter_eco_temp"]),
                away_temp=float(user_input["winter_away_temp"]),
                night_temp=float(user_input["winter_night_temp"]),
                inactive_temp=float(user_input["winter_inactive_temp"]),
                winter_comfort_temp=float(user_input["winter_comfort_temp"]),
                winter_eco_temp=float(user_input["winter_eco_temp"]),
                winter_away_temp=float(user_input["winter_away_temp"]),
                winter_night_temp=float(user_input["winter_night_temp"]),
                winter_inactive_temp=float(user_input["winter_inactive_temp"]),
                summer_comfort_temp=float(user_input["summer_comfort_temp"]),
                summer_eco_temp=float(user_input["summer_eco_temp"]),
                summer_away_temp=float(user_input["summer_away_temp"]),
                summer_night_temp=float(user_input["summer_night_temp"]),
                summer_inactive_temp=float(user_input["summer_inactive_temp"]),
                schedule_enabled=bool(user_input["schedule_enabled"]),
                schedule_rules=current.schedule_rules,
                schedule_rules_winter=current.schedule_rules_winter,
                schedule_rules_summer=current.schedule_rules_summer,
            )
            await storage.async_update_global_config(
                updated,
                previous_global_config=current,
            )
            return await self._async_finish_and_reload()

        return self.async_show_form(
            step_id="global_settings",
            data_schema=_global_schema(storage.config.global_config),
        )

    async def async_step_season_settings(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        from dataclasses import replace as dataclass_replace

        storage = await self._async_storage()
        if user_input is not None:
            current = storage.config.global_config
            updated = dataclass_replace(current, season_mode=str(user_input["season_mode"]).lower())
            await storage.async_update_global_config(updated, previous_global_config=current)
            return await self._async_finish_and_reload()

        current_mode = storage.config.global_config.season_mode
        schema = vol.Schema(
            {
                vol.Required("season_mode", default=current_mode): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=_selector_options(SEASON_MODES),
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="season_settings", data_schema=schema)

    async def async_step_add_zone(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self._async_step_zone_policy(user_input, zone=None)

    async def async_step_edit_zone_select(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        storage = await self._async_storage()
        zones = list(storage.config.zones)
        if not zones:
            return await self._async_finish_and_reload()
        if user_input is not None:
            self._editing_zone_id = user_input["zone_id"]
            return await self.async_step_zone_policy()
        return self.async_show_form(
            step_id="edit_zone_select",
            data_schema=vol.Schema(
                {
                    vol.Required("zone_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=_zone_selector_options(zones),
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def _async_step_zone_policy(
        self,
        user_input: dict[str, Any] | None = None,
        *,
        zone: ZoneDefinition | None,
    ) -> FlowResult:
        storage = await self._async_storage()
        current_zone = zone
        if current_zone is None and self._editing_zone_id:
            current_zone = storage.get_zone(self._editing_zone_id)
        if current_zone is None:
            defaults = storage.config.global_config
            current_zone = ZoneDefinition(
                zone_id="",
                name="",
                custom_setpoints=False,
                custom_schedule=False,
                schedule_enabled=bool(defaults.schedule_enabled),
                schedule_rules=list(defaults.schedule_rules),
                schedule_rules_winter=list(defaults.schedule_rules_winter),
                schedule_rules_summer=list(defaults.schedule_rules_summer),
                comfort_temp=float(defaults.comfort_temp),
                eco_temp=float(defaults.eco_temp),
                away_temp=float(defaults.away_temp),
                night_temp=float(defaults.night_temp),
                inactive_temp=float(defaults.inactive_temp),
            )
        errors: dict[str, str] = {}
        if user_input is not None:
            zone_id = str(user_input["zone_id"]).strip()
            if not zone_id:
                errors["base"] = "invalid_zone"
            elif (
                zone_id != (current_zone.zone_id if current_zone else None)
                and storage.get_zone(zone_id) is not None
            ):
                errors["base"] = "duplicate_zone"
            else:
                updated_zone = ZoneDefinition(
                    zone_id=zone_id,
                    name=str(user_input["name"]).strip(),
                    current_temperature=current_zone.current_temperature if current_zone else None,
                    current_humidity=current_zone.current_humidity if current_zone else None,
                    target_temperature=current_zone.target_temperature if current_zone else None,
                    hvac_mode=current_zone.hvac_mode if current_zone else None,
                    status_register=current_zone.status_register if current_zone else None,
                    people=list(user_input.get("people", [])),
                    presence_sensor=user_input.get("presence_sensor") or None,
                    is_common_area=bool(user_input["is_common_area"]),
                    enabled=bool(user_input["enabled"]),
                    manual_override_allowed=bool(user_input["manual_override_allowed"]),
                    manual_override_temp=current_zone.manual_override_temp if current_zone else None,
                    manual_override_until=current_zone.manual_override_until if current_zone else None,
                    custom_setpoints=bool(user_input.get("custom_setpoints", False)),
                    custom_schedule=bool(user_input.get("custom_schedule", False)),
                    schedule_enabled=bool(user_input.get("schedule_enabled", True)),
                    schedule_rules=current_zone.schedule_rules if current_zone else [],
                    schedule_rules_winter=current_zone.schedule_rules_winter if current_zone else [],
                    schedule_rules_summer=current_zone.schedule_rules_summer if current_zone else [],
                    comfort_temp=float(user_input["comfort_temp"]),
                    eco_temp=float(user_input["eco_temp"]),
                    away_temp=float(user_input["away_temp"]),
                    night_temp=float(user_input["night_temp"]),
                    inactive_temp=float(user_input["inactive_temp"]),
                )
                if updated_zone.custom_schedule and not current_zone.custom_schedule:
                    global_config = storage.config.global_config
                    updated_zone.schedule_enabled = bool(global_config.schedule_enabled)
                    updated_zone.schedule_rules = list(global_config.schedule_rules)
                    updated_zone.schedule_rules_winter = list(global_config.schedule_rules_winter)
                    updated_zone.schedule_rules_summer = list(global_config.schedule_rules_summer)
                if not updated_zone.custom_setpoints:
                    global_config = storage.config.global_config
                    updated_zone.comfort_temp = float(global_config.comfort_temp)
                    updated_zone.eco_temp = float(global_config.eco_temp)
                    updated_zone.away_temp = float(global_config.away_temp)
                    updated_zone.night_temp = float(global_config.night_temp)
                    updated_zone.inactive_temp = float(global_config.inactive_temp)
                await storage.async_upsert_zone(updated_zone)
                if current_zone and current_zone.zone_id != zone_id:
                    await storage.async_delete_zone(current_zone.zone_id)
                self._editing_zone_id = None
                return await self._async_finish_and_reload()

        return self.async_show_form(
            step_id="add_zone" if zone is None and not self._editing_zone_id else "zone_policy",
            data_schema=_zone_policy_schema(self.hass, current_zone),
            errors=errors,
        )

    async def async_step_zone_policy(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self._async_step_zone_policy(user_input, zone=None)

    async def async_step_edit_zone_mapping_select(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        storage = await self._async_storage()
        zones = list(storage.config.zones)
        if not zones:
            return await self._async_finish_and_reload()
        if user_input is not None:
            self._editing_zone_id = user_input["zone_id"]
            return await self.async_step_zone_mapping()
        return self.async_show_form(
            step_id="edit_zone_mapping_select",
            data_schema=vol.Schema(
                {
                    vol.Required("zone_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=_zone_selector_options(zones),
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_zone_mapping(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        storage = await self._async_storage()
        current_zone = storage.get_zone(self._editing_zone_id) if self._editing_zone_id else None
        if current_zone is None:
            self._editing_zone_id = None
            return await self._async_finish_and_reload()
        if user_input is not None:
            zone = ZoneDefinition(
                zone_id=current_zone.zone_id,
                name=current_zone.name,
                current_temperature=_build_register("current", user_input),
                current_humidity=_build_register("humidity", user_input),
                target_temperature=_build_register("target", user_input),
                hvac_mode=_build_register("hvac", user_input),
                status_register=current_zone.status_register,
                people=current_zone.people,
                presence_sensor=current_zone.presence_sensor,
                is_common_area=current_zone.is_common_area,
                enabled=current_zone.enabled,
                manual_override_allowed=current_zone.manual_override_allowed,
                manual_override_temp=current_zone.manual_override_temp,
                manual_override_until=current_zone.manual_override_until,
                custom_setpoints=current_zone.custom_setpoints,
                custom_schedule=current_zone.custom_schedule,
                schedule_enabled=current_zone.schedule_enabled,
                schedule_rules=current_zone.schedule_rules,
                schedule_rules_winter=current_zone.schedule_rules_winter,
                schedule_rules_summer=current_zone.schedule_rules_summer,
                comfort_temp=current_zone.comfort_temp,
                eco_temp=current_zone.eco_temp,
                away_temp=current_zone.away_temp,
                night_temp=current_zone.night_temp,
                inactive_temp=current_zone.inactive_temp,
            )
            await storage.async_upsert_zone(zone)
            self._editing_zone_id = None
            return await self._async_finish_and_reload()

        return self.async_show_form(
            step_id="zone_mapping",
            data_schema=_zone_mapping_schema(current_zone),
        )

    async def async_step_delete_zone_select(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        storage = await self._async_storage()
        zones = list(storage.config.zones)
        if not zones:
            return await self._async_finish_and_reload()
        if user_input is not None:
            await storage.async_delete_zone(user_input["zone_id"])
            return await self._async_finish_and_reload()
        return self.async_show_form(
            step_id="delete_zone_select",
            data_schema=vol.Schema(
                {
                    vol.Required("zone_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=_zone_selector_options(zones),
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    @staticmethod
    def _season_step_suffix(season: str) -> str:
        return "summer" if season == SEASON_MODE_SUMMER else "winter"

    def _schedule_rules_for_season(self, season: str, global_config: GlobalConfig) -> list[ScheduleRule]:
        if season == SEASON_MODE_SUMMER:
            return list(global_config.schedule_rules_summer)
        if global_config.schedule_rules_winter:
            return list(global_config.schedule_rules_winter)
        return list(global_config.schedule_rules)

    def _apply_schedule_rules_for_season(
        self,
        season: str,
        global_config: GlobalConfig,
        rules: list[ScheduleRule],
    ) -> None:
        if season == SEASON_MODE_SUMMER:
            global_config.schedule_rules_summer = rules
        else:
            global_config.schedule_rules_winter = rules
        # Keep legacy field aligned for backward compatibility.
        if season == SEASON_MODE_WINTER or not global_config.schedule_rules:
            global_config.schedule_rules = list(global_config.schedule_rules_winter)

    async def async_step_add_schedule(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self.async_step_add_schedule_winter(user_input)

    async def async_step_add_schedule_winter(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        self._editing_schedule_season = SEASON_MODE_WINTER
        self._editing_schedule_id = None
        return await self._async_step_schedule(
            user_input,
            rule=None,
            season=SEASON_MODE_WINTER,
        )

    async def async_step_add_schedule_summer(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        self._editing_schedule_season = SEASON_MODE_SUMMER
        self._editing_schedule_id = None
        return await self._async_step_schedule(
            user_input,
            rule=None,
            season=SEASON_MODE_SUMMER,
        )

    async def _async_step_edit_schedule_select_for_season(
        self,
        season: str,
        step_id: str,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        storage = await self._async_storage()
        rules = self._schedule_rules_for_season(season, storage.config.global_config)
        rule_ids = [rule.rule_id for rule in rules]
        if not rule_ids:
            return await self._async_finish_and_reload()
        if user_input is not None:
            self._editing_schedule_season = season
            self._editing_schedule_id = user_input["rule_id"]
            return await self._async_step_schedule(
                None,
                rule=None,
                season=season,
            )
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required("rule_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=_selector_options(rule_ids),
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_edit_schedule_select(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self.async_step_edit_schedule_select_winter(user_input)

    async def async_step_edit_schedule_select_winter(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self._async_step_edit_schedule_select_for_season(
            SEASON_MODE_WINTER,
            "edit_schedule_select_winter",
            user_input,
        )

    async def async_step_edit_schedule_select_summer(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self._async_step_edit_schedule_select_for_season(
            SEASON_MODE_SUMMER,
            "edit_schedule_select_summer",
            user_input,
        )

    async def _async_step_schedule(
        self,
        user_input: dict[str, Any] | None = None,
        *,
        rule: ScheduleRule | None,
        season: str,
    ) -> FlowResult:
        storage = await self._async_storage()
        rules = self._schedule_rules_for_season(season, storage.config.global_config)
        current_rule = rule
        if current_rule is None and self._editing_schedule_id:
            current_rule = next(
                (
                    candidate
                    for candidate in rules
                    if candidate.rule_id == self._editing_schedule_id
                ),
                None,
            )
        errors: dict[str, str] = {}
        if user_input is not None:
            rule_id = str(user_input["rule_id"]).strip()
            existing_ids = {candidate.rule_id for candidate in rules}
            if (
                rule_id != (current_rule.rule_id if current_rule else None)
                and rule_id in existing_ids
            ):
                errors["base"] = "duplicate_schedule"
            else:
                new_rule = ScheduleRule(
                    rule_id=rule_id,
                    name=str(user_input["name"]).strip(),
                    days=list(user_input["days"]),
                    start=str(user_input["start"]),
                    end=str(user_input["end"]),
                    mode=str(user_input["mode"]).lower(),
                )
                updated_rules = [
                    candidate
                    for candidate in rules
                    if candidate.rule_id != rule_id
                    and candidate.rule_id != self._editing_schedule_id
                ]
                updated_rules.append(new_rule)
                global_config = storage.config.global_config
                self._apply_schedule_rules_for_season(season, global_config, updated_rules)
                await storage.async_update_global_config(global_config)
                self._editing_schedule_id = None
                return await self._async_finish_and_reload()

        suffix = self._season_step_suffix(season)
        return self.async_show_form(
            step_id=f"add_schedule_{suffix}" if current_rule is None else f"schedule_{suffix}",
            data_schema=_schedule_schema(current_rule),
            errors=errors,
        )

    async def async_step_schedule(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self.async_step_schedule_winter(user_input)

    async def async_step_schedule_winter(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        self._editing_schedule_season = SEASON_MODE_WINTER
        return await self._async_step_schedule(
            user_input,
            rule=None,
            season=SEASON_MODE_WINTER,
        )

    async def async_step_schedule_summer(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        self._editing_schedule_season = SEASON_MODE_SUMMER
        return await self._async_step_schedule(
            user_input,
            rule=None,
            season=SEASON_MODE_SUMMER,
        )

    async def _async_step_delete_schedule_select_for_season(
        self,
        season: str,
        step_id: str,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        storage = await self._async_storage()
        rules = self._schedule_rules_for_season(season, storage.config.global_config)
        if not rules:
            return await self._async_finish_and_reload()
        if user_input is not None:
            global_config = storage.config.global_config
            updated = [rule for rule in rules if rule.rule_id != user_input["rule_id"]]
            self._apply_schedule_rules_for_season(season, global_config, updated)
            await storage.async_update_global_config(global_config)
            return await self._async_finish_and_reload()
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required("rule_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=_selector_options([rule.rule_id for rule in rules]),
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_delete_schedule_select(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self.async_step_delete_schedule_select_winter(user_input)

    async def async_step_delete_schedule_select_winter(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self._async_step_delete_schedule_select_for_season(
            SEASON_MODE_WINTER,
            "delete_schedule_select_winter",
            user_input,
        )

    async def async_step_delete_schedule_select_summer(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self._async_step_delete_schedule_select_for_season(
            SEASON_MODE_SUMMER,
            "delete_schedule_select_summer",
            user_input,
        )

    def _zone_schedule_rules_for_season(self, zone: ZoneDefinition, season: str) -> list[ScheduleRule]:
        if season == SEASON_MODE_SUMMER:
            rules = list(zone.schedule_rules_summer)
        else:
            rules = list(zone.schedule_rules_winter)
        return rules if rules else list(zone.schedule_rules)

    def _apply_zone_schedule_rules_for_season(
        self,
        zone: ZoneDefinition,
        season: str,
        rules: list[ScheduleRule],
    ) -> None:
        if season == SEASON_MODE_SUMMER:
            zone.schedule_rules_summer = rules
        else:
            zone.schedule_rules_winter = rules
        if season == SEASON_MODE_WINTER or not zone.schedule_rules:
            zone.schedule_rules = list(zone.schedule_rules_winter)

    async def _async_step_zone_schedule_pick_zone(
        self,
        season: str,
        step_id: str,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        if user_input is None:
            self._editing_zone_schedule_zone_id = None
            self._editing_zone_schedule_id = None
        storage = await self._async_storage()
        zones = list(storage.config.zones)
        if not zones:
            return await self._async_finish_and_reload()
        if user_input is not None and user_input.get("zone_id"):
            self._editing_zone_schedule_zone_id = user_input["zone_id"]
            self._editing_zone_schedule_id = None
            if season == SEASON_MODE_SUMMER:
                return await self.async_step_zone_schedule_summer()
            return await self.async_step_zone_schedule_winter()
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required("zone_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=_zone_selector_options(zones),
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_add_zone_schedule_winter(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self._async_step_zone_schedule_pick_zone(
            SEASON_MODE_WINTER,
            "add_zone_schedule_winter",
            user_input,
        )

    async def async_step_add_zone_schedule_summer(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self._async_step_zone_schedule_pick_zone(
            SEASON_MODE_SUMMER,
            "add_zone_schedule_summer",
            user_input,
        )

    async def _async_step_edit_zone_schedule_select_for_season(
        self,
        season: str,
        step_id: str,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        storage = await self._async_storage()
        zones = list(storage.config.zones)
        if not zones:
            return await self._async_finish_and_reload()

        if self._editing_zone_schedule_zone_id is None:
            if user_input is not None and user_input.get("zone_id"):
                self._editing_zone_schedule_zone_id = user_input["zone_id"]
            else:
                return self.async_show_form(
                    step_id=step_id,
                    data_schema=vol.Schema(
                        {
                            vol.Required("zone_id"): selector.SelectSelector(
                                selector.SelectSelectorConfig(
                                    options=_zone_selector_options(zones),
                                    mode=selector.SelectSelectorMode.DROPDOWN,
                                )
                            )
                        }
                    ),
                )

        zone = storage.get_zone(self._editing_zone_schedule_zone_id)
        if zone is None:
            self._editing_zone_schedule_zone_id = None
            return await self._async_finish_and_reload()
        rules = self._zone_schedule_rules_for_season(zone, season)
        if not rules:
            self._editing_zone_schedule_zone_id = None
            return await self._async_finish_and_reload()

        if user_input is not None and user_input.get("rule_id"):
            self._editing_zone_schedule_id = user_input["rule_id"]
            if season == SEASON_MODE_SUMMER:
                return await self.async_step_zone_schedule_summer()
            return await self.async_step_zone_schedule_winter()

        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required("rule_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=_selector_options([rule.rule_id for rule in rules]),
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_edit_zone_schedule_select_winter(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is None:
            self._editing_zone_schedule_zone_id = None
            self._editing_zone_schedule_id = None
        return await self._async_step_edit_zone_schedule_select_for_season(
            SEASON_MODE_WINTER,
            "edit_zone_schedule_select_winter",
            user_input,
        )

    async def async_step_edit_zone_schedule_select_summer(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is None:
            self._editing_zone_schedule_zone_id = None
            self._editing_zone_schedule_id = None
        return await self._async_step_edit_zone_schedule_select_for_season(
            SEASON_MODE_SUMMER,
            "edit_zone_schedule_select_summer",
            user_input,
        )

    async def _async_step_zone_schedule(
        self,
        season: str,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        storage = await self._async_storage()
        if not self._editing_zone_schedule_zone_id:
            return await self._async_finish_and_reload()
        zone = storage.get_zone(self._editing_zone_schedule_zone_id)
        if zone is None:
            self._editing_zone_schedule_zone_id = None
            self._editing_zone_schedule_id = None
            return await self._async_finish_and_reload()

        rules = self._zone_schedule_rules_for_season(zone, season)
        current_rule = None
        if self._editing_zone_schedule_id:
            current_rule = next(
                (
                    candidate
                    for candidate in rules
                    if candidate.rule_id == self._editing_zone_schedule_id
                ),
                None,
            )

        errors: dict[str, str] = {}
        if user_input is not None:
            rule_id = str(user_input["rule_id"]).strip()
            existing_ids = {candidate.rule_id for candidate in rules}
            if (
                rule_id != (current_rule.rule_id if current_rule else None)
                and rule_id in existing_ids
            ):
                errors["base"] = "duplicate_schedule"
            else:
                zone.custom_schedule = True
                zone.schedule_enabled = bool(zone.schedule_enabled)
                new_rule = ScheduleRule(
                    rule_id=rule_id,
                    name=str(user_input["name"]).strip(),
                    days=list(user_input["days"]),
                    start=str(user_input["start"]),
                    end=str(user_input["end"]),
                    mode=str(user_input["mode"]).lower(),
                )
                updated_rules = [
                    candidate
                    for candidate in rules
                    if candidate.rule_id != rule_id
                    and candidate.rule_id != self._editing_zone_schedule_id
                ]
                updated_rules.append(new_rule)
                self._apply_zone_schedule_rules_for_season(zone, season, updated_rules)
                await storage.async_upsert_zone(zone)
                self._editing_zone_schedule_id = None
                self._editing_zone_schedule_zone_id = None
                return await self._async_finish_and_reload()

        suffix = self._season_step_suffix(season)
        return self.async_show_form(
            step_id=f"zone_schedule_{suffix}",
            data_schema=_schedule_schema(current_rule),
            errors=errors,
        )

    async def async_step_zone_schedule_winter(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self._async_step_zone_schedule(SEASON_MODE_WINTER, user_input)

    async def async_step_zone_schedule_summer(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self._async_step_zone_schedule(SEASON_MODE_SUMMER, user_input)

    async def _async_step_delete_zone_schedule_select_for_season(
        self,
        season: str,
        step_id: str,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        storage = await self._async_storage()
        zones = list(storage.config.zones)
        if not zones:
            return await self._async_finish_and_reload()

        if self._editing_zone_schedule_zone_id is None:
            if user_input is not None and user_input.get("zone_id"):
                self._editing_zone_schedule_zone_id = user_input["zone_id"]
            else:
                return self.async_show_form(
                    step_id=step_id,
                    data_schema=vol.Schema(
                        {
                            vol.Required("zone_id"): selector.SelectSelector(
                                selector.SelectSelectorConfig(
                                    options=_zone_selector_options(zones),
                                    mode=selector.SelectSelectorMode.DROPDOWN,
                                )
                            )
                        }
                    ),
                )

        zone = storage.get_zone(self._editing_zone_schedule_zone_id)
        if zone is None:
            self._editing_zone_schedule_zone_id = None
            return await self._async_finish_and_reload()
        rules = self._zone_schedule_rules_for_season(zone, season)
        if not rules:
            self._editing_zone_schedule_zone_id = None
            return await self._async_finish_and_reload()

        if user_input is not None and user_input.get("rule_id"):
            updated_rules = [rule for rule in rules if rule.rule_id != user_input["rule_id"]]
            self._apply_zone_schedule_rules_for_season(zone, season, updated_rules)
            await storage.async_upsert_zone(zone)
            self._editing_zone_schedule_zone_id = None
            self._editing_zone_schedule_id = None
            return await self._async_finish_and_reload()

        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required("rule_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=_selector_options([rule.rule_id for rule in rules]),
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_delete_zone_schedule_select_winter(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is None:
            self._editing_zone_schedule_zone_id = None
            self._editing_zone_schedule_id = None
        return await self._async_step_delete_zone_schedule_select_for_season(
            SEASON_MODE_WINTER,
            "delete_zone_schedule_select_winter",
            user_input,
        )

    async def async_step_delete_zone_schedule_select_summer(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is None:
            self._editing_zone_schedule_zone_id = None
            self._editing_zone_schedule_id = None
        return await self._async_step_delete_zone_schedule_select_for_season(
            SEASON_MODE_SUMMER,
            "delete_zone_schedule_select_summer",
            user_input,
        )

    async def async_step_import_legacy_yaml(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        defaults = {**self.config_entry.data, **self.config_entry.options}
        errors: dict[str, str] = {}
        if user_input is not None:
            storage = await self._async_storage()
            try:
                load_zone_map(self.hass, user_input[CONF_ZONE_MAP_PATH])
                await storage.async_import_yaml(user_input[CONF_ZONE_MAP_PATH])
            except ZoneMapError:
                errors["base"] = "invalid_zone_map"
            else:
                return await self._async_finish_and_reload()

        return self.async_show_form(
            step_id="import_legacy_yaml",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ZONE_MAP_PATH,
                        default=defaults.get(CONF_ZONE_MAP_PATH, DEFAULT_ZONE_MAP_PATH),
                    ): str
                }
            ),
            errors=errors,
        )
