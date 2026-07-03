"""Coordinator for Termogea."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TermogeaApiError, TermogeaClient
from .const import (
    GLOBAL_SEASON_REGISTER_MOD,
    GLOBAL_SEASON_REGISTER_REG,
    GLOBAL_SEASON_VALUE_SUMMER,
    SEASON_SUMMER,
    SEASON_WINTER,
)
from .models import RegisterDefinition, ZoneDefinition, ZoneSnapshot
from .policy import compute_dew_point

_LOGGER = logging.getLogger(__name__)

# Registro globale Season (sola lettura) cablato: mod=10, reg=99, scale 1.
# La centralina espone qui la stagione operativa (0=inverno, 1=estate).
_GLOBAL_SEASON_REGISTER = RegisterDefinition(
    mod=GLOBAL_SEASON_REGISTER_MOD,
    reg=GLOBAL_SEASON_REGISTER_REG,
    scale=1.0,
    precision=0,
)


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
        # Stagione operativa osservata dal registro globale della centralina
        # ("winter"/"summer"). None finche' non e' stata letta almeno una volta.
        self.observed_season: str | None = None

    async def _async_read_observed_season(self) -> None:
        """Read the global Season register and cache the observed season.

        Fail-safe: in caso di errore di lettura si mantiene l'ultimo valore noto
        (o None), senza far fallire l'intero ciclo di aggiornamento.
        """
        try:
            raw, _value = await self.client.async_read_register(_GLOBAL_SEASON_REGISTER)
        except TermogeaApiError as err:
            _LOGGER.warning(
                "Termogea global Season register read failed, keeping previous value: %s",
                err,
            )
            return
        if raw is None:
            return
        self.observed_season = (
            SEASON_SUMMER if raw == GLOBAL_SEASON_VALUE_SUMMER else SEASON_WINTER
        )

    async def _async_update_data(self) -> dict[str, ZoneSnapshot]:
        try:
            try:
                await self.client.async_check_thcontrol_status()
            except TermogeaApiError as err:
                # Do not fail the whole update cycle when status endpoint is flaky.
                _LOGGER.warning("Termogea status check failed, continuing with cached session: %s", err)
            await self._async_read_observed_season()
            snapshots: dict[str, ZoneSnapshot] = {}
            for zone in self.zones:
                previous = self.data.get(zone.zone_id) if isinstance(self.data, dict) else None
                raw_values: dict[str, int | None] = (
                    dict(previous.raw_values) if previous is not None else {}
                )
                current_value = previous.current_temperature if previous is not None else None
                humidity_value = previous.current_humidity if previous is not None else None
                target_value = previous.target_temperature if previous is not None else None
                hvac_mode = previous.hvac_mode if previous is not None else None
                status_value = previous.status_value if previous is not None else None

                if zone.current_temperature is not None:
                    try:
                        current_raw, current_value = await self.client.async_read_register(
                            zone.current_temperature
                        )
                        raw_values["current_temperature"] = current_raw
                    except TermogeaApiError as err:
                        _LOGGER.warning(
                            "Zone %s current temperature read failed: %s",
                            zone.zone_id,
                            err,
                        )

                if zone.current_humidity is not None:
                    try:
                        humidity_raw, humidity_value = await self.client.async_read_register(
                            zone.current_humidity
                        )
                        raw_values["current_humidity"] = humidity_raw
                        if humidity_raw in (None, 0, 65535):
                            humidity_value = None
                        elif humidity_value is not None and not (0.0 < humidity_value <= 100.0):
                            # Some controllers expose humidity in tenths even when
                            # register metadata scale is 1.0 (e.g. 552 => 55.2%).
                            if 0 < humidity_raw <= 1000:
                                humidity_value = round(float(humidity_raw) / 10.0, 1)
                            else:
                                humidity_value = None
                    except TermogeaApiError as err:
                        _LOGGER.warning(
                            "Zone %s humidity read failed: %s",
                            zone.zone_id,
                            err,
                        )

                if zone.target_temperature is not None:
                    try:
                        target_raw, target_value = await self.client.async_read_register(
                            zone.target_temperature
                        )
                        raw_values["target_temperature"] = target_raw
                    except TermogeaApiError as err:
                        _LOGGER.warning(
                            "Zone %s target temperature read failed: %s",
                            zone.zone_id,
                            err,
                        )

                if zone.hvac_mode is not None:
                    try:
                        hvac_raw, _ = await self.client.async_read_register(zone.hvac_mode)
                        raw_values["hvac_mode"] = hvac_raw
                        if hvac_raw == zone.hvac_mode.off_value:
                            hvac_mode = "off"
                        elif hvac_raw == zone.hvac_mode.heat_value:
                            hvac_mode = "heat"
                    except TermogeaApiError as err:
                        _LOGGER.warning(
                            "Zone %s hvac mode read failed: %s",
                            zone.zone_id,
                            err,
                        )

                if zone.status_register is not None:
                    try:
                        status_raw, _ = await self.client.async_read_register(zone.status_register)
                        raw_values["zone_status"] = status_raw
                        status_value = status_raw
                    except TermogeaApiError as err:
                        _LOGGER.warning(
                            "Zone %s status register read failed: %s",
                            zone.zone_id,
                            err,
                        )

                # Punto di rugiada per zona: calcolato solo quando temperatura e
                # umidita' sono valide. humidity_value e' gia' None quando il
                # raw era 65535/0/None (normalizzazione a monte).
                dew_point = compute_dew_point(current_value, humidity_value)

                snapshots[zone.zone_id] = ZoneSnapshot(
                    current_temperature=current_value,
                    current_humidity=humidity_value,
                    target_temperature=target_value,
                    hvac_mode=hvac_mode,
                    status_value=status_value,
                    raw_values=raw_values,
                    season=self.observed_season,
                    dew_point=dew_point,
                )

            if not snapshots and isinstance(self.data, dict):
                return self.data
            return snapshots
        except TermogeaApiError as err:
            raise UpdateFailed(str(err)) from err
