"""Entity helpers for consistent device metadata."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .models import ZoneDefinition


def controller_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Build the device info for the Termogea controller."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Termogea",
        model="Season Controller",
    )


def zone_device_info(entry: ConfigEntry, zone: ZoneDefinition) -> DeviceInfo:
    """Build the device info for one Termogea zone.

    Includes a legacy identifier for existing installations.
    """
    return DeviceInfo(
        identifiers={
            (DOMAIN, f"{entry.entry_id}_zone_{zone.zone_id}"),
            (DOMAIN, f"{entry.entry_id}_{zone.zone_id}_device"),
            (DOMAIN, f"termogea_{zone.zone_id}_device"),
            (DOMAIN, f"Termogea_{zone.zone_id}_device"),
            (DOMAIN, f"{zone.zone_id}_device"),
        },
        name=zone.name or zone.zone_id,
        manufacturer="Termogea",
        model="Zone Controller",
        via_device=(DOMAIN, entry.entry_id),
    )
