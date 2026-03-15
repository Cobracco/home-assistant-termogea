"""The Termogea integration."""

from __future__ import annotations

import logging
import re
from ipaddress import ip_address

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr

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
    SERVICE_IMPORT_CONTROLLER_CONFIG,
    SERVICE_IMPORT_LEGACY_YAML,
)
from .coordinator import TermogeaDataUpdateCoordinator
from .models import RegisterDefinition, ZoneDefinition
from .policy import evaluate_zone_policy
from .storage_manager import TermogeaStorageManager
from .zone_map import ZoneMapError

_LOGGER = logging.getLogger(__name__)


def _zone_identifiers(entry_id: str, zone_id: str) -> tuple[str, ...]:
    return (
        f"{entry_id}_zone_{zone_id}",
        f"{entry_id}_{zone_id}_device",
        f"termogea_{zone_id}_device",
        f"Termogea_{zone_id}_device",
        f"{zone_id}_device",
    )


def _looks_like_legacy_name(name: str) -> bool:
    lowered = name.strip().lower()
    return lowered.startswith("termogea_") or lowered.endswith("_device")


def _zone_index(zone_id: str) -> int | None:
    for pattern in (r"(?:zone|zona)\D*(\d+)", r"(\d+)"):
        match = re.search(pattern, zone_id, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _is_ipv4_or_ipv6(value: str) -> bool:
    try:
        ip_address(value)
    except ValueError:
        return False
    return True


def _resolve_entry_host(entry: ConfigEntry) -> tuple[str | None, bool]:
    """Return best host candidate and whether entry data should be updated."""
    current_host = str(entry.data.get(CONF_HOST, "")).strip()
    if current_host and current_host != entry.data.get(CONF_HOST):
        return current_host, True

    if current_host and entry.title and current_host.lower() == entry.title.strip().lower():
        unique_id = (entry.unique_id or "").strip()
        if unique_id and unique_id != current_host and _is_ipv4_or_ipv6(unique_id):
            return unique_id, True

    if current_host:
        return current_host, False

    unique_id = (entry.unique_id or "").strip()
    if unique_id:
        return unique_id, True

    return None, False


def _looks_like_default_zone_name(name: str, zone_id: str) -> bool:
    lowered = name.strip().lower()
    if not lowered:
        return True
    if lowered == zone_id.strip().lower():
        return True
    return (
        lowered.startswith("termogea_")
        or lowered.startswith("zona ")
        or lowered.startswith("zone ")
        or lowered.endswith("_device")
    )


async def _sync_zone_names_from_controller(
    storage: TermogeaStorageManager,
    client: TermogeaClient,
) -> None:
    """Import human-friendly zone names from the Termogea setup page."""
    try:
        remote_names = await client.async_fetch_zone_names()
    except TermogeaApiError:
        return
    if not remote_names:
        return

    changed = False
    for zone in storage.config.zones:
        idx = _zone_index(zone.zone_id)
        if idx is None:
            continue
        remote_name = remote_names.get(idx)
        if not remote_name:
            continue
        if _looks_like_default_zone_name(zone.name, zone.zone_id) and zone.name != remote_name:
            zone.name = remote_name
            changed = True

    if changed:
        await storage.async_save()


async def _bootstrap_storage_from_controller(
    storage: TermogeaStorageManager,
    client: TermogeaClient,
) -> bool:
    """Initialize persistent config from Termogea controller files."""
    try:
        global_config, zones = await client.async_fetch_controller_bootstrap()
    except TermogeaApiError:
        return False
    if not zones:
        return False
    storage.config.global_config = global_config
    storage.config.zones = zones
    await storage.async_save()
    return True


async def _sync_zone_humidity_mapping_from_controller(
    storage: TermogeaStorageManager,
    client: TermogeaClient,
) -> None:
    """Backfill humidity register mapping for already configured zones."""
    try:
        _global, imported_zones = await client.async_fetch_controller_bootstrap()
    except TermogeaApiError:
        return
    if not imported_zones:
        return

    imported_humidity: dict[int, object] = {}
    for imported in imported_zones:
        idx = _zone_index(imported.zone_id)
        if idx is None or imported.current_humidity is None:
            continue
        imported_humidity[idx] = imported.current_humidity

    if not imported_humidity:
        return

    changed = False
    for zone in storage.config.zones:
        if zone.current_humidity is not None:
            continue
        idx = _zone_index(zone.zone_id)
        if idx is None:
            continue
        humidity_register = imported_humidity.get(idx)
        if humidity_register is not None:
            zone.current_humidity = humidity_register
            changed = True

    if changed:
        await storage.async_save()


def _same_mod_reg(a: RegisterDefinition | None, b: RegisterDefinition | None) -> bool:
    if a is None or b is None:
        return False
    return a.mod == b.mod and a.reg == b.reg


async def _is_humidity_register_readable(
    client: TermogeaClient,
    register: RegisterDefinition,
) -> bool:
    try:
        _raw, value = await client.async_read_register(register)
    except TermogeaApiError:
        return False
    if value is None:
        return False
    # Humidity values out of physical range are considered invalid mapping.
    return 0.0 <= value <= 100.0


async def _repair_zone_humidity_mapping_from_controller(
    storage: TermogeaStorageManager,
    client: TermogeaClient,
) -> None:
    """Repair stale humidity mapping by validating current vs controller mapping."""
    try:
        _global, imported_zones = await client.async_fetch_controller_bootstrap()
    except TermogeaApiError:
        return
    if not imported_zones:
        return

    imported_humidity: dict[int, RegisterDefinition] = {}
    for imported in imported_zones:
        idx = _zone_index(imported.zone_id)
        if idx is None or imported.current_humidity is None:
            continue
        imported_humidity[idx] = imported.current_humidity

    if not imported_humidity:
        return

    changed = False
    for zone in storage.config.zones:
        idx = _zone_index(zone.zone_id)
        if idx is None:
            continue
        imported_register = imported_humidity.get(idx)
        if imported_register is None:
            continue

        current_register = zone.current_humidity
        if current_register is None:
            zone.current_humidity = imported_register
            changed = True
            continue

        if _same_mod_reg(current_register, imported_register):
            continue

        current_ok = await _is_humidity_register_readable(client, current_register)
        if current_ok:
            continue

        imported_ok = await _is_humidity_register_readable(client, imported_register)
        if imported_ok:
            zone.current_humidity = imported_register
            changed = True

    if changed:
        await storage.async_save()


def _sync_zone_device_names(hass: HomeAssistant, entry: ConfigEntry, zones: list[ZoneDefinition]) -> None:
    """Align legacy zone device names with configured zone names."""
    registry = dr.async_get(hass)
    for zone in zones:
        desired = (zone.name or zone.zone_id).strip() or zone.zone_id
        device = None
        for identifier in _zone_identifiers(entry.entry_id, zone.zone_id):
            device = registry.async_get_device(identifiers={(DOMAIN, identifier)})
            if device is not None:
                break
        if device is None:
            continue
        current_name = device.name or ""
        update_kwargs: dict[str, str] = {}

        if device.name_by_user is None:
            if _looks_like_legacy_name(current_name) and current_name != desired:
                update_kwargs["name"] = desired
        elif _looks_like_legacy_name(device.name_by_user) and device.name_by_user != desired:
            update_kwargs["name_by_user"] = desired
            if current_name != desired:
                update_kwargs["name"] = desired

        if update_kwargs:
            registry.async_update_device(device.id, **update_kwargs)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries to the latest version."""
    if entry.version > 3:
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
        entry = hass.config_entries.async_get_entry(entry.entry_id) or entry

    if entry.version == 2:
        migrated_data = dict(entry.data)
        migrated_options = dict(entry.options)
        connection_keys = (
            CONF_HOST,
            CONF_USERNAME,
            CONF_PASSWORD,
            CONF_SCAN_INTERVAL,
            CONF_REQUEST_TIMEOUT,
            CONF_ZONE_MAP_PATH,
        )

        for key in connection_keys:
            if key not in migrated_data and key in migrated_options:
                migrated_data[key] = migrated_options[key]
            migrated_options.pop(key, None)

        migrated_data.setdefault(CONF_ZONE_MAP_PATH, DEFAULT_ZONE_MAP_PATH)
        migrated_data.setdefault(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        migrated_data.setdefault(CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT)

        hass.config_entries.async_update_entry(
            entry,
            data=migrated_data,
            options=migrated_options,
            version=3,
        )
        _LOGGER.info("Migrated Termogea config entry %s from version 2 to 3", entry.entry_id)

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

    async def _import_controller_config_service(_call: ServiceCall) -> None:
        for entry_id, entry_data in hass.data[DOMAIN].items():
            client: TermogeaClient = entry_data[DATA_CLIENT]
            storage: TermogeaStorageManager = entry_data[DATA_STORAGE]
            imported = await _bootstrap_storage_from_controller(storage, client)
            if imported:
                await hass.config_entries.async_reload(entry_id)

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
        hass.services.async_register(
            DOMAIN,
            SERVICE_IMPORT_CONTROLLER_CONFIG,
            _import_controller_config_service,
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
    connection_keys = (
        CONF_HOST,
        CONF_USERNAME,
        CONF_PASSWORD,
        CONF_SCAN_INTERVAL,
        CONF_REQUEST_TIMEOUT,
        CONF_ZONE_MAP_PATH,
    )
    if any(key in entry.options for key in connection_keys):
        migrated_data = dict(entry.data)
        migrated_options = dict(entry.options)
        changed = False
        for key in connection_keys:
            if key in migrated_options:
                migrated_data.setdefault(key, migrated_options[key])
                migrated_options.pop(key, None)
                changed = True
        if changed:
            hass.config_entries.async_update_entry(
                entry,
                data=migrated_data,
                options=migrated_options,
            )

    host, should_update_host = _resolve_entry_host(entry)
    username = entry.data.get(CONF_USERNAME)
    password = entry.data.get(CONF_PASSWORD)
    if not host or not username or not password:
        raise ConfigEntryNotReady(
            "Missing required Termogea connection settings (host/username/password). "
            "Open the integration and run Reconfigure."
        )
    if should_update_host:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_HOST: host},
        )

    zone_map_path = entry.data.get(CONF_ZONE_MAP_PATH, DEFAULT_ZONE_MAP_PATH)
    scan_interval = int(entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
    timeout = int(entry.data.get(CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT))

    storage = TermogeaStorageManager(hass, entry.entry_id)
    try:
        await storage.async_load()
        if not storage.config.zones:
            try:
                await storage.async_initialize_from_yaml(zone_map_path)
            except ZoneMapError as err:
                # In v1 persistent mode the legacy YAML is optional: startup must continue.
                if "Zone map file not found:" in str(err):
                    _LOGGER.info(
                        "Termogea legacy YAML not found at %s; starting with empty persistent config",
                        zone_map_path,
                    )
                else:
                    raise
    except ZoneMapError as err:
        raise ConfigEntryNotReady(str(err)) from err

    client = TermogeaClient(
        async_get_clientsession(hass),
        host,
        username,
        password,
        timeout,
    )

    try:
        await client.async_login()
        await client.async_check_thcontrol_status()
        if not storage.config.zones:
            imported = await _bootstrap_storage_from_controller(storage, client)
            if imported:
                _LOGGER.info(
                    "Initialized Termogea config from controller (%s zones imported)",
                    len(storage.config.zones),
                )
        if storage.config.zones:
            await _sync_zone_names_from_controller(storage, client)
            if any(zone.current_humidity is None for zone in storage.config.zones):
                await _sync_zone_humidity_mapping_from_controller(storage, client)
            await _repair_zone_humidity_mapping_from_controller(storage, client)
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
    _sync_zone_device_names(hass, entry, storage.config.zones)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
