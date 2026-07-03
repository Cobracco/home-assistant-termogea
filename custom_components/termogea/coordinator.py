"""Coordinator for Termogea."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TermogeaApiError, TermogeaClient
from .const import (
    SEASON_SUMMER,
    SEASON_WINTER,
)
from .models import ZoneDefinition, ZoneSnapshot
from .policy import compute_dew_point

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
        # Stagione operativa osservata dai registri season per-zona (caldo/freddo).
        # None finche' non e' stata letta almeno una volta.
        self.observed_season: str | None = None

    async def _async_read_observed_season(self) -> None:
        """Determina la stagione osservata dai registri ZoneN season affidabili.

        Il registro globale 10/99 e' un "Dummy" di scratch che la centralina
        riscrive di continuo (valore instabile 0/1): NON e' un indicatore di
        stagione affidabile. Si leggono invece i registri season per-zona
        (base+6) delle zone che supportano il raffrescamento -- le uniche
        coerenti caldo/freddo -- e si prende la maggioranza.
        Fail-safe: su errore o assenza di dati affidabili si mantiene l'ultimo
        valore noto, senza far fallire il ciclo di aggiornamento.
        """
        summer_votes = 0
        winter_votes = 0
        for zone in self.zones:
            if not zone.supports_cooling:
                continue
            register = zone.season_register
            if (
                register is None
                or register.summer_value is None
                or register.winter_value is None
            ):
                continue
            try:
                raw, _value = await self.client.async_read_register(register)
            except TermogeaApiError as err:
                _LOGGER.warning(
                    "Zone %s season register read failed: %s", zone.zone_id, err
                )
                continue
            if raw is None:
                continue
            if raw == register.summer_value:
                summer_votes += 1
            elif raw == register.winter_value:
                winter_votes += 1
        if summer_votes == 0 and winter_votes == 0:
            # Nessun dato affidabile: mantieni l'ultimo valore noto.
            return
        self.observed_season = (
            SEASON_SUMMER if summer_votes >= winter_votes else SEASON_WINTER
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
