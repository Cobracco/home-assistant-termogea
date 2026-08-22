"""Policy evaluation for Termogea zones."""

from __future__ import annotations

import math
from datetime import time

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    GLOBAL_MODE_AUTO,
    GLOBAL_MODE_AWAY,
    GLOBAL_MODE_COMFORT,
    GLOBAL_MODE_ECO,
    GLOBAL_MODE_NIGHT,
    GLOBAL_MODE_OFF,
    MIN_VALID_HUMIDITY_PCT,
    POLICY_REASON_COOLING_NOT_SUPPORTED,
    SEASON_MODE_SUMMER,
    SEASON_MODE_WINTER,
)
from .models import GlobalConfig, PolicyDecision, ZoneDefinition, ZoneSnapshot

# Coefficienti Magnus-Tetens per il calcolo del punto di rugiada.
_MAGNUS_A = 17.62
_MAGNUS_B = 243.12


def compute_dew_point(temp_c: float | None, rh_pct: float | None) -> float | None:
    """Calcola il punto di rugiada (°C) con la formula di Magnus-Tetens.

    Ritorna None quando temperatura o umidita' non sono valide (assenti, RH
    fuori dall'intervallo plausibile MIN_VALID_HUMIDITY_PCT-100%). Una RH
    implausibilmente bassa (es. 1% da un registro mappato male) darebbe un
    dew point assurdo che azzererebbe il floor anticondensa. Il risultato e'
    arrotondato a un decimale, coerente con la precisione dei sensori.
    """
    if temp_c is None or rh_pct is None:
        return None
    try:
        temp = float(temp_c)
        rh = float(rh_pct)
    except (TypeError, ValueError):
        return None
    if not MIN_VALID_HUMIDITY_PCT <= rh <= 100.0:
        return None
    gamma = (_MAGNUS_A * temp) / (_MAGNUS_B + temp) + math.log(rh / 100.0)
    dew_point = (_MAGNUS_B * gamma) / (_MAGNUS_A - gamma)
    return round(dew_point, 1)


def _state(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    return None if state is None else state.state


def _is_on(hass: HomeAssistant, entity_id: str) -> bool:
    return (_state(hass, entity_id) or "").lower() in {
        "on",
        "home",
        "true",
        "occupied",
        "detected",
    }


def _house_people_present(hass: HomeAssistant, zones: list[ZoneDefinition]) -> bool:
    people = {person for zone in zones for person in zone.people}
    return any(_is_on(hass, person) for person in people)


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(hour=int(hour), minute=int(minute))


def resolve_active_season(
    settings: GlobalConfig,
    observed_season: str | None = None,
) -> str:
    """Resolve the active season (winter/summer).

    Priorita': override manuale (season_mode winter/summer) > stagione osservata
    dal registro globale della centralina (observed_season) > fallback sul mese
    corrente quando la stagione osservata non e' disponibile.
    """
    configured = (settings.season_mode or "").lower()
    if configured in {SEASON_MODE_WINTER, SEASON_MODE_SUMMER}:
        return configured

    observed = (observed_season or "").lower()
    if observed in {SEASON_MODE_WINTER, SEASON_MODE_SUMMER}:
        return observed

    month = dt_util.now().month
    return SEASON_MODE_SUMMER if 4 <= month <= 9 else SEASON_MODE_WINTER


def conditioning_is_cooling(season: str | None) -> bool:
    """Return True quando la stagione operativa e' l'estate (raffrescamento)."""
    return (season or "").lower() == SEASON_MODE_SUMMER


def _global_schedule_rules_for_season(settings: GlobalConfig, season: str):
    if season == SEASON_MODE_SUMMER:
        rules = settings.schedule_rules_summer
    else:
        rules = settings.schedule_rules_winter
    if rules:
        return rules
    return settings.schedule_rules


def _zone_schedule_rules_for_season(zone: ZoneDefinition, season: str):
    if season == SEASON_MODE_SUMMER:
        rules = zone.schedule_rules_summer
    else:
        rules = zone.schedule_rules_winter
    if rules:
        return rules
    return zone.schedule_rules


def _season_mode_value(settings: GlobalConfig, season: str, mode: str) -> float:
    if mode == GLOBAL_MODE_COMFORT:
        return (
            settings.summer_comfort_temp
            if season == SEASON_MODE_SUMMER
            else settings.winter_comfort_temp
        )
    if mode == GLOBAL_MODE_ECO:
        return (
            settings.summer_eco_temp
            if season == SEASON_MODE_SUMMER
            else settings.winter_eco_temp
        )
    if mode == GLOBAL_MODE_AWAY:
        return (
            settings.summer_away_temp
            if season == SEASON_MODE_SUMMER
            else settings.winter_away_temp
        )
    if mode == GLOBAL_MODE_NIGHT:
        return (
            settings.summer_night_temp
            if season == SEASON_MODE_SUMMER
            else settings.winter_night_temp
        )
    return (
        settings.summer_inactive_temp
        if season == SEASON_MODE_SUMMER
        else settings.winter_inactive_temp
    )


def _zone_mode_value(zone: ZoneDefinition, season: str, mode: str) -> float:
    """Resolve the per-zone setpoint for one mode, honouring the season.

    In estate i setpoint di raffrescamento sono i campi summer_* della zona;
    in inverno quelli invernali. Senza questa distinzione una zona con
    custom_setpoints applicherebbe i setpoint invernali anche in raffrescamento.
    """
    if season == SEASON_MODE_SUMMER:
        if mode == GLOBAL_MODE_COMFORT:
            return zone.summer_comfort_temp
        if mode == GLOBAL_MODE_ECO:
            return zone.summer_eco_temp
        if mode == GLOBAL_MODE_AWAY:
            return zone.summer_away_temp
        if mode == GLOBAL_MODE_NIGHT:
            return zone.summer_night_temp
        return zone.summer_inactive_temp
    if mode == GLOBAL_MODE_COMFORT:
        return zone.comfort_temp
    if mode == GLOBAL_MODE_ECO:
        return zone.eco_temp
    if mode == GLOBAL_MODE_AWAY:
        return zone.away_temp
    if mode == GLOBAL_MODE_NIGHT:
        return zone.night_temp
    return zone.inactive_temp


def _seasonal_zone_target(zone: ZoneDefinition, settings: GlobalConfig, season: str, mode: str) -> float:
    """Resolve effective target for one zone and one mode."""
    if not zone.custom_setpoints:
        return _season_mode_value(settings, season, mode)
    # Custom zone temperatures are absolute values and must not be shifted by
    # legacy/global deltas, otherwise runtime setpoint changes "bounce back".
    return _zone_mode_value(zone, season, mode)


def _active_manual_override_target(zone: ZoneDefinition) -> float | None:
    """Return manual override target when active, otherwise None."""
    if not zone.manual_override_allowed:
        return None
    if zone.manual_override_temp is None or not zone.manual_override_until:
        return None

    until = dt_util.parse_datetime(zone.manual_override_until)
    if until is None:
        return None
    if until.tzinfo is None:
        until = until.replace(tzinfo=dt_util.UTC)
    if dt_util.utcnow() >= dt_util.as_utc(until):
        return None
    return float(zone.manual_override_temp)


def resolve_active_mode(
    settings: GlobalConfig,
    zone: ZoneDefinition | None = None,
    observed_season: str | None = None,
) -> str:
    """Resolve the effective active mode including schedule."""
    mode = settings.global_mode.lower()
    if mode != GLOBAL_MODE_AUTO:
        return mode

    active_season = resolve_active_season(settings, observed_season)
    schedule_enabled = settings.schedule_enabled
    schedule_rules = _global_schedule_rules_for_season(settings, active_season)
    if zone is not None and zone.custom_schedule:
        schedule_enabled = zone.schedule_enabled
        zone_rules = _zone_schedule_rules_for_season(zone, active_season)
        schedule_rules = zone_rules or schedule_rules

    if not schedule_enabled:
        return settings.auto_fallback_mode

    now = dt_util.now()
    weekday = now.strftime("%a").lower()[:3]
    current = now.time()
    if zone is not None and zone.custom_schedule:
        zone_rules = _zone_schedule_rules_for_season(zone, active_season)
        schedule_rules = zone_rules or _global_schedule_rules_for_season(settings, active_season)
    else:
        schedule_rules = _global_schedule_rules_for_season(settings, active_season)

    for rule in schedule_rules:
        if weekday not in rule.days:
            continue
        start = _parse_hhmm(rule.start)
        end = _parse_hhmm(rule.end)
        if start <= end:
            if start <= current <= end:
                return rule.mode
        else:
            if current >= start or current <= end:
                return rule.mode

    return settings.auto_fallback_mode


def evaluate_zone_policy(
    hass: HomeAssistant,
    zone: ZoneDefinition,
    zones: list[ZoneDefinition],
    settings: GlobalConfig,
    observed_season: str | None = None,
    *,
    dew_point: float | None = None,
) -> PolicyDecision:
    """Compute the current policy decision for a zone.

    La stagione operativa deriva da observed_season (registro globale) e da un
    eventuale override manuale. In estate su zone senza raffrescamento si va a
    riposo; con RH valida si applica il clamp anticondensa al setpoint.
    """
    decision = _evaluate_zone_policy_core(
        hass, zone, zones, settings, observed_season
    )
    return _apply_seasonal_adjustments(
        zone, settings, observed_season, decision, dew_point=dew_point
    )


def _apply_seasonal_adjustments(
    zone: ZoneDefinition,
    settings: GlobalConfig,
    observed_season: str | None,
    decision: PolicyDecision,
    *,
    dew_point: float | None,
) -> PolicyDecision:
    """Applica gli aggiustamenti stagionali estivi alla decisione grezza.

    - Zona senza raffrescamento in estate: va a riposo (zone_enabled=False,
      reason 'cooling_not_supported', setpoint neutro estivo).
    - Clamp anticondensa: in raffrescamento con RH valida, il setpoint effettivo
      non scende sotto dew_point + margine (alza il setpoint, mai lo abbassa).
    """
    active_season = resolve_active_season(settings, observed_season)
    cooling = conditioning_is_cooling(active_season)
    if not cooling:
        return decision

    # In estate una zona che non supporta il raffrescamento va sempre a riposo,
    # a prescindere dalla presenza: la centralina non controlla il freddo qui.
    if not zone.supports_cooling:
        return PolicyDecision(
            assigned_people_present=decision.assigned_people_present,
            presence_detected=decision.presence_detected,
            zone_enabled=False,
            policy_reason=POLICY_REASON_COOLING_NOT_SUPPORTED,
            effective_target=_seasonal_zone_target(
                zone, settings, active_season, GLOBAL_MODE_OFF
            ),
            active_mode=decision.active_mode,
        )

    # Clamp anticondensa: solo su zone attive in raffrescamento, con protezione
    # abilitata, RH disponibile (dew_point calcolato) e setpoint definito.
    if (
        decision.zone_enabled
        and settings.dewpoint_protection_enabled
        and dew_point is not None
        and decision.effective_target is not None
    ):
        # Difesa in profondita': un margine negativo (storage manipolato)
        # non deve mai portare il floor sotto il dew point puro.
        floor = dew_point + max(0.0, settings.dewpoint_margin)
        if decision.effective_target < floor:
            return PolicyDecision(
                assigned_people_present=decision.assigned_people_present,
                presence_detected=decision.presence_detected,
                zone_enabled=decision.zone_enabled,
                policy_reason=decision.policy_reason,
                effective_target=round(floor, 1),
                active_mode=decision.active_mode,
            )

    return decision


def _evaluate_zone_policy_core(
    hass: HomeAssistant,
    zone: ZoneDefinition,
    zones: list[ZoneDefinition],
    settings: GlobalConfig,
    observed_season: str | None = None,
) -> PolicyDecision:
    """Compute the current policy decision for a zone."""

    assigned_people_present = any(_is_on(hass, person) for person in zone.people)
    presence_detected = bool(zone.presence_sensor and _is_on(hass, zone.presence_sensor))
    house_people_present = _house_people_present(hass, zones)
    active_season = resolve_active_season(settings, observed_season)
    active_mode = resolve_active_mode(settings, zone, observed_season)

    if not zone.enabled:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=False,
            policy_reason="zone_disabled",
            effective_target=_seasonal_zone_target(zone, settings, active_season, GLOBAL_MODE_OFF),
            active_mode=active_mode,
        )

    if not settings.global_enabled or not settings.automations_enabled:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=False,
            policy_reason="global_disabled",
            effective_target=_seasonal_zone_target(zone, settings, active_season, GLOBAL_MODE_OFF),
            active_mode=active_mode,
        )

    # Hard gate requested for v1 UX: when nobody is home every zone goes to the
    # same conservation temperature, regardless of per-zone assignment.
    if not house_people_present:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=False,
            policy_reason="home_empty_conservation",
            effective_target=_season_mode_value(settings, active_season, GLOBAL_MODE_OFF),
            active_mode=active_mode,
        )

    if zone.is_common_area:
        people_gate = house_people_present or assigned_people_present
        eligible = people_gate or (settings.allow_common_without_people and presence_detected)
    else:
        eligible = assigned_people_present

    if not eligible:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=False,
            policy_reason="no_people_assigned_home",
            effective_target=_season_mode_value(settings, active_season, GLOBAL_MODE_OFF),
            active_mode=active_mode,
        )

    if active_mode == GLOBAL_MODE_OFF:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=False,
            policy_reason="global_off",
            effective_target=_seasonal_zone_target(zone, settings, active_season, GLOBAL_MODE_OFF),
            active_mode=active_mode,
        )

    if active_mode == GLOBAL_MODE_AWAY:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=False,
            policy_reason="global_away",
            effective_target=_seasonal_zone_target(zone, settings, active_season, GLOBAL_MODE_AWAY),
            active_mode=active_mode,
        )

    manual_override_target = _active_manual_override_target(zone)
    if manual_override_target is not None:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=True,
            policy_reason="manual_override",
            effective_target=manual_override_target,
            active_mode=active_mode,
        )

    # Local presence has operational priority: if a zone is eligible and its
    # room sensor is active, force comfort target regardless of eco/night mode.
    if presence_detected:
        return PolicyDecision(
            assigned_people_present=assigned_people_present,
            presence_detected=presence_detected,
            zone_enabled=True,
            policy_reason="presence_comfort",
            effective_target=_seasonal_zone_target(zone, settings, active_season, GLOBAL_MODE_COMFORT),
            active_mode=active_mode,
        )

    # Expected behavior:
    # - at least one assigned person in home => zone in ECO
    # - local presence active => zone in COMFORT
    # Global schedule/mode keeps being exposed as metadata, but does not
    # downgrade eligible occupied zones to night/away temperatures.
    return PolicyDecision(
        assigned_people_present=assigned_people_present,
        presence_detected=presence_detected,
        zone_enabled=True,
        policy_reason="assigned_people_eco",
        effective_target=_seasonal_zone_target(zone, settings, active_season, GLOBAL_MODE_ECO),
        active_mode=active_mode,
    )

    # Fallback kept for backward compatibility, currently unreachable because
    # all eligible zones are resolved above.
    return PolicyDecision(
        assigned_people_present=assigned_people_present,
        presence_detected=presence_detected,
        zone_enabled=True,
        policy_reason="eligible_without_local_presence",
        effective_target=_seasonal_zone_target(zone, settings, active_season, GLOBAL_MODE_ECO),
        active_mode=active_mode,
    )


def is_zone_conditioning_active(
    snapshot: ZoneSnapshot | None,
    decision: PolicyDecision,
    *,
    cooling: bool = False,
    delta_celsius: float = 0.1,
) -> bool:
    """Return True when the zone is actively demanding conditioning.

    La direzione della domanda dipende dalla stagione: in riscaldamento la zona
    e' attiva quando la temperatura misurata e' sotto il setpoint
    (current < target - delta); in raffrescamento (cooling=True) la direzione e'
    invertita (current > target + delta).
    """
    if snapshot is None:
        return False
    if not decision.zone_enabled:
        return False
    if snapshot.hvac_mode == "off":
        return False

    # Lo StatusBits (bit0) della centralina rappresenta la richiesta di
    # RISCALDAMENTO della zona e resta 0 in raffrescamento: usarlo solo in
    # riscaldamento. In estate (cooling) la domanda si deduce dal confronto
    # temperatura corrente/target piu' sotto (current > target + delta).
    if not cooling and snapshot.status_value is not None:
        # Server-provided StatusBits: bit0 represents active zone request.
        return bool(snapshot.status_value & 0x0001)

    current = snapshot.current_temperature
    target = snapshot.target_temperature
    if current is None:
        return False
    if target is None:
        target = decision.effective_target
    if target is None:
        return False
    if cooling:
        return current > (target + delta_celsius)
    return current < (target - delta_celsius)


# Alias retrocompatibile: manteniamo il nome storico per non rompere gli import
# esistenti (climate.py, binary_sensor.py). Semantica invariata (riscaldamento).
def is_zone_heating_active(
    snapshot: ZoneSnapshot | None,
    decision: PolicyDecision,
    *,
    delta_celsius: float = 0.1,
) -> bool:
    """Backward-compatible alias for is_zone_conditioning_active (heating)."""
    return is_zone_conditioning_active(
        snapshot, decision, cooling=False, delta_celsius=delta_celsius
    )
