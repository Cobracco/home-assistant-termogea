"""The Termogea integration."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TermogeaApiError, TermogeaAuthError, TermogeaClient
from .const import (
    ATTR_ZONE_ID,
    CONF_REQUEST_TIMEOUT,
    CONF_SCAN_INTERVAL,
    CONF_ZONE_MAP_PATH,
    DATA_CLIENT,
    DATA_COORDINATOR,
    DATA_STORAGE,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_ZONE_MAP_PATH,
    DOMAIN,
    PLATFORMS,
    SERVICE_APPLY_ALL_ZONE_POLICIES,
    SERVICE_APPLY_ZONE_POLICY,
    SERVICE_FORCE_RELOGIN,
    SERVICE_IMPORT_LEGACY_YAML,
)
from .coordinator import TermogeaDataUpdateCoordinator
from .models import ZoneDefinition
from .policy import evaluate_zone_policy
from .storage_manager import TermogeaStorageManager
from .zone_map import ZoneMapError

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries to the latest version."""
    if entry.version > 2:
        _LOGGER.error(
            "Cannot migrate Termogea entry %s: unsupported future version %s",
            entry.entry_id,
            entry.version,
        )
        return False

    if entry.version == 1:
        migrated_data = dict(entry.data)
        migrated_data.setdefault(CONF_ZONE_MAP_PATH, DEFAULT_ZONE_MAP_PATH)
        migrated_data.setdefault(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        migrated_data.setdefault(CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT)
        hass.config_entries.async_update_entry(
            entry,
            data=migrated_data,
            version=2,
        )
        _LOGGER.info("Migrated Termogea config entry %s from version 1 to 2", entry.entry_id)

    return True


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Set up services for the integration."""
    hass.data.setdefault(DOMAIN, {})

    async def _import_legacy_yaml_service(call: ServiceCall) -> None:
        path = call.data[CONF_ZONE_MAP_PATH]
        for entry_id, entry_data in hass.data[DOMAIN].items():
            storage: TermogeaStorageManager = entry_data[DATA_STORAGE]
            await storage.async_import_yaml(path)
            await hass.config_entries.async_reload(entry_id)

    async def _force_relogin_service(_call: ServiceCall) -> None:
        for entry_data in hass.data[DOMAIN].values():
            client: TermogeaClient = entry_data[DATA_CLIENT]
            await client.async_force_relogin()
            await entry_data[DATA_COORDINATOR].async_request_refresh()

    async def _apply_policy(zone: ZoneDefinition, entry_data: dict) -> None:
        coordinator: TermogeaDataUpdateCoordinator = entry_data[DATA_COORDINATOR]
        client: TermogeaClient = entry_data[DATA_CLIENT]
        storage: TermogeaStorageManager = entry_data[DATA_STORAGE]
        decision = evaluate_zone_policy(
            hass,
            zone,
            storage.config.zones,
            storage.config.global_config,
        )

        if zone.target_temperature is None:
            return

        if decision.zone_enabled and decision.effective_target is not None:
            await client.async_write_scaled_register(
                zone.target_temperature,
                decision.effective_target,
            )
            if zone.hvac_mode and zone.hvac_mode.heat_value is not None:
                await client.async_write_register_value(
                    zone.hvac_mode,
                    zone.hvac_mode.heat_value,
                )
        else:
            if zone.hvac_mode and zone.hvac_mode.off_value is not None:
                await client.async_write_register_value(
                    zone.hvac_mode,
                    zone.hvac_mode.off_value,
                )
            elif decision.effective_target is not None:
                await client.async_write_scaled_register(
                    zone.target_temperature,
                    decision.effective_target,
                )

        await coordinator.async_request_refresh()

    async def _apply_zone_policy_service(call: ServiceCall) -> None:
        zone_id = call.data[ATTR_ZONE_ID]
        for entry_data in hass.data[DOMAIN].values():
            storage: TermogeaStorageManager = entry_data[DATA_STORAGE]
            zone = storage.get_zone(zone_id)
            if zone is not None:
                await _apply_policy(zone, entry_data)
                return
        raise HomeAssistantError(f"Unknown Termogea zone_id: {zone_id}")

    async def _apply_all_zone_policies_service(_call: ServiceCall) -> None:
        for entry_data in hass.data[DOMAIN].values():
            storage: TermogeaStorageManager = entry_data[DATA_STORAGE]
            for zone in storage.config.zones:
                await _apply_policy(zone, entry_data)

    if not hass.services.has_service(DOMAIN, SERVICE_IMPORT_LEGACY_YAML):
        hass.services.async_register(
            DOMAIN,
            SERVICE_IMPORT_LEGACY_YAML,
            _import_legacy_yaml_service,
            schema=vol.Schema({vol.Required(CONF_ZONE_MAP_PATH): str}),
        )
        hass.services.async_register(DOMAIN, SERVICE_FORCE_RELOGIN, _force_relogin_service)
        hass.services.async_register(
            DOMAIN,
            SERVICE_APPLY_ZONE_POLICY,
            _apply_zone_policy_service,
            schema=vol.Schema({vol.Required(ATTR_ZONE_ID): str}),
        )
        hass.services.async_register(
            DOMAIN,
            SERVICE_APPLY_ALL_ZONE_POLICIES,
            _apply_all_zone_policies_service,
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Termogea from a config entry."""
    options = {**entry.data, **entry.options}
    zone_map_path = options.get(CONF_ZONE_MAP_PATH, DEFAULT_ZONE_MAP_PATH)
    scan_interval = options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    timeout = options.get(CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT)

    storage = TermogeaStorageManager(hass, entry.entry_id)
    try:
        await storage.async_load()
        if not storage.config.zones:
            await storage.async_initialize_from_yaml(zone_map_path)
    except ZoneMapError as err:
        raise ConfigEntryNotReady(str(err)) from err

    client = TermogeaClient(
        async_get_clientsession(hass),
        options[CONF_HOST],
        options[CONF_USERNAME],
        options[CONF_PASSWORD],
        timeout,
    )

    try:
        await client.async_login()
        await client.async_check_thcontrol_status()
    except TermogeaAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except TermogeaApiError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = TermogeaDataUpdateCoordinator(hass, client, storage.config.zones, scan_interval)
    coordinator.config_entry = entry
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_CLIENT: client,
        DATA_COORDINATOR: coordinator,
        DATA_STORAGE: storage,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
