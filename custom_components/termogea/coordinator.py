"""Coordinator for Termogea."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TermogeaApiError, TermogeaClient
from .models import ZoneDefinition, ZoneSnapshot

_LOGGER = logging.getLogger(__name__)


class TermogeaDataUpdateCoordinator(DataUpdateCoordinator[dict[str, ZoneSnapshot]]):
    """Coordinate data fetching from Termogea."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: TermogeaClient,
        zones: list[ZoneDefinition],
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name="termogea",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.zones = zones

    async def _async_update_data(self) -> dict[str, ZoneSnapshot]:
        try:
            await self.client.async_check_thcontrol_status()
            snapshots: dict[str, ZoneSnapshot] = {}
            for zone in self.zones:
                previous = self.data.get(zone.zone_id) if isinstance(self.data, dict) else None
                raw_values: dict[str, int | None] = (
                    dict(previous.raw_values) if previous is not None else {}
                )
                current_value = previous.current_temperature if previous is not None else None
                target_value = previous.target_temperature if previous is not None else None
                hvac_mode = previous.hvac_mode if previous is not None else None

                try:
                    if zone.current_temperature is not None:
                        current_raw, current_value = await self.client.async_read_register(
                            zone.current_temperature
                        )
                        raw_values["current_temperature"] = current_raw

                    if zone.target_temperature is not None:
                        target_raw, target_value = await self.client.async_read_register(
                            zone.target_temperature
                        )
                        raw_values["target_temperature"] = target_raw

                    if zone.hvac_mode is not None:
                        hvac_raw, _ = await self.client.async_read_register(zone.hvac_mode)
                        raw_values["hvac_mode"] = hvac_raw
                        if hvac_raw == zone.hvac_mode.off_value:
                            hvac_mode = "off"
                        elif hvac_raw == zone.hvac_mode.heat_value:
                            hvac_mode = "heat"
                except TermogeaApiError as err:
                    _LOGGER.warning(
                        "Zone %s read failed, keeping last known snapshot: %s",
                        zone.zone_id,
                        err,
                    )

                snapshots[zone.zone_id] = ZoneSnapshot(
                    current_temperature=current_value,
                    target_temperature=target_value,
                    hvac_mode=hvac_mode,
                    raw_values=raw_values,
                )

            return snapshots
        except TermogeaApiError as err:
            raise UpdateFailed(str(err)) from err
