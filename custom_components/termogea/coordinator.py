"""Coordinator for Termogea."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TermogeaApiError, TermogeaClient, normalize_humidity_reading
from .const import (
    SEASON_SUMMER,
    SEASON_WINTER,
)
from .models import RegisterDefinition, ZoneDefinition, ZoneSnapshot
from .policy import compute_dew_point

_LOGGER = logging.getLogger(__name__)

# Chiavi dei campi registro letti per ogni zona in un ciclo di aggiornamento.
_FIELD_SEASON = "season"
_FIELD_CURRENT_TEMPERATURE = "current_temperature"
_FIELD_CURRENT_HUMIDITY = "current_humidity"
_FIELD_TARGET_TEMPERATURE = "target_temperature"
_FIELD_HVAC_MODE = "hvac_mode"
_FIELD_ZONE_STATUS = "zone_status"


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
        # Zone gia' segnalate per letture umidita' implausibili (warn-once).
        self._implausible_humidity_warned: set[str] = set()

    def _collect_reads(self) -> list[tuple[str, str, RegisterDefinition]]:
        """Elenca tutte le letture registro del ciclo come (zone_id, campo, registro)."""
        reads: list[tuple[str, str, RegisterDefinition]] = []
        for zone in self.zones:
            season_register = zone.season_register
            if (
                zone.supports_cooling
                and season_register is not None
                and season_register.summer_value is not None
                and season_register.winter_value is not None
            ):
                reads.append((zone.zone_id, _FIELD_SEASON, season_register))
            if zone.current_temperature is not None:
                reads.append(
                    (zone.zone_id, _FIELD_CURRENT_TEMPERATURE, zone.current_temperature)
                )
            if zone.current_humidity is not None:
                reads.append(
                    (zone.zone_id, _FIELD_CURRENT_HUMIDITY, zone.current_humidity)
                )
            if zone.target_temperature is not None:
                reads.append(
                    (zone.zone_id, _FIELD_TARGET_TEMPERATURE, zone.target_temperature)
                )
            if zone.hvac_mode is not None:
                reads.append((zone.zone_id, _FIELD_HVAC_MODE, zone.hvac_mode))
            if zone.status_register is not None:
                reads.append((zone.zone_id, _FIELD_ZONE_STATUS, zone.status_register))
        return reads

    async def _async_read_all(
        self,
        reads: list[tuple[str, str, RegisterDefinition]],
    ) -> dict[tuple[str, str], tuple[int | None, float | None]]:
        """Legge tutti i registri del ciclo, in batch quando possibile.

        Ritorna {(zone_id, campo): (raw, valore)}. Le letture fallite non
        compaiono nel risultato: il chiamante mantiene il valore precedente.
        Se il batch fallisce si degrada alle letture singole per-registro.
        """
        values: dict[tuple[str, str], tuple[int | None, float | None]] = {}
        if not reads:
            return values

        try:
            results = await self.client.async_read_registers(
                [register for _, _, register in reads]
            )
        except TermogeaApiError as err:
            _LOGGER.warning(
                "Batch register read failed (%s), falling back to per-register reads",
                err,
            )
        else:
            for (zone_id, field_name, _), result in zip(reads, results):
                values[(zone_id, field_name)] = result
            return values

        failures = 0
        for zone_id, field_name, register in reads:
            try:
                values[(zone_id, field_name)] = await self.client.async_read_register(
                    register
                )
            except TermogeaApiError as err:
                failures += 1
                _LOGGER.warning(
                    "Zone %s %s read failed: %s", zone_id, field_name, err
                )
                if not values and failures >= 3:
                    # Controller irraggiungibile: ogni altro tentativo pagherebbe
                    # l'intero timeout HTTP senza speranza di successo.
                    _LOGGER.warning(
                        "First %s register reads failed with no success, "
                        "aborting update cycle",
                        failures,
                    )
                    break
        return values

    def _update_observed_season(
        self,
        values: dict[tuple[str, str], tuple[int | None, float | None]],
    ) -> None:
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
            register = zone.season_register
            if register is None:
                continue
            result = values.get((zone.zone_id, _FIELD_SEASON))
            if result is None:
                continue
            raw, _value = result
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

    def _normalized_humidity(
        self,
        zone: ZoneDefinition,
        raw: int | None,
        value: float | None,
    ) -> float | None:
        """Normalizza la RH di zona segnalando (una volta) i valori implausibili."""
        normalized = normalize_humidity_reading(raw, value)
        if normalized is None and raw not in (None, 0, 65535):
            if zone.zone_id not in self._implausible_humidity_warned:
                self._implausible_humidity_warned.add(zone.zone_id)
                register = zone.current_humidity
                _LOGGER.warning(
                    "Zone %s humidity register mod=%s reg=%s returned implausible "
                    "value (raw=%s): reading discarded, check the humidity register "
                    "mapping for this zone",
                    zone.zone_id,
                    register.mod if register is not None else None,
                    register.reg if register is not None else None,
                    raw,
                )
        elif normalized is not None:
            self._implausible_humidity_warned.discard(zone.zone_id)
        return normalized

    async def _async_update_data(self) -> dict[str, ZoneSnapshot]:
        try:
            try:
                await self.client.async_check_thcontrol_status()
            except TermogeaApiError as err:
                # Do not fail the whole update cycle when status endpoint is flaky.
                _LOGGER.warning(
                    "Termogea status check failed, continuing with cached session: %s",
                    err,
                )

            reads = self._collect_reads()
            values = await self._async_read_all(reads)
            if reads and not values:
                # Nessuna lettura riuscita: il controller e' irraggiungibile.
                # Meglio marcare le entita' non disponibili che congelare
                # indefinitamente gli ultimi valori noti.
                raise UpdateFailed("No Termogea register could be read")

            self._update_observed_season(values)

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

                result = values.get((zone.zone_id, _FIELD_CURRENT_TEMPERATURE))
                if result is not None:
                    current_raw, current_value = result
                    raw_values["current_temperature"] = current_raw

                result = values.get((zone.zone_id, _FIELD_CURRENT_HUMIDITY))
                if result is not None:
                    humidity_raw, humidity_scaled = result
                    raw_values["current_humidity"] = humidity_raw
                    humidity_value = self._normalized_humidity(
                        zone, humidity_raw, humidity_scaled
                    )

                result = values.get((zone.zone_id, _FIELD_TARGET_TEMPERATURE))
                if result is not None:
                    target_raw, target_value = result
                    raw_values["target_temperature"] = target_raw

                result = values.get((zone.zone_id, _FIELD_HVAC_MODE))
                if result is not None and zone.hvac_mode is not None:
                    hvac_raw, _ = result
                    raw_values["hvac_mode"] = hvac_raw
                    if hvac_raw == zone.hvac_mode.off_value:
                        hvac_mode = "off"
                    elif hvac_raw == zone.hvac_mode.heat_value:
                        hvac_mode = "heat"

                result = values.get((zone.zone_id, _FIELD_ZONE_STATUS))
                if result is not None:
                    status_raw, _ = result
                    raw_values["zone_status"] = status_raw
                    status_value = status_raw

                # Punto di rugiada per zona: calcolato solo quando temperatura e
                # umidita' sono valide. humidity_value e' gia' None quando il
                # raw era una sentinella o una lettura implausibile.
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
