"""Constants for the Termogea integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "termogea"
PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SELECT,
    Platform.SWITCH,
]

CONF_SCAN_INTERVAL = "scan_interval"
CONF_ZONE_MAP_PATH = "zone_map_path"
CONF_REQUEST_TIMEOUT = "request_timeout"

DEFAULT_SCAN_INTERVAL = 30
DEFAULT_REQUEST_TIMEOUT = 10
DEFAULT_ZONE_MAP_PATH = "termogea_zones.yaml"

MIN_SCAN_INTERVAL = 10
MAX_SCAN_INTERVAL = 300

DATA_CLIENT = "client"
DATA_COORDINATOR = "coordinator"
DATA_ZONE_MAP = "zone_map"
DATA_STORAGE = "storage"

SERVICE_IMPORT_LEGACY_YAML = "import_legacy_yaml"
SERVICE_IMPORT_CONTROLLER_CONFIG = "import_controller_config"
SERVICE_FORCE_RELOGIN = "force_relogin"
SERVICE_APPLY_ZONE_POLICY = "apply_zone_policy"
SERVICE_APPLY_ALL_ZONE_POLICIES = "apply_all_zone_policies"
SERVICE_SET_ZONE_ENABLED = "set_zone_enabled"

ATTR_ZONE_ID = "zone_id"
ATTR_ASSIGNED_PEOPLE = "assigned_people"
ATTR_PRESENCE_SENSOR = "presence_sensor"
ATTR_IS_COMMON_AREA = "is_common_area"
ATTR_POLICY_REASON = "policy_reason"
ATTR_EFFECTIVE_TARGET = "effective_target"
ATTR_ASSIGNED_PEOPLE_PRESENT = "assigned_people_present"
ATTR_PRESENCE_DETECTED = "presence_detected"
ATTR_ZONE_ENABLED = "zone_enabled"
ATTR_HEATING_ACTIVE = "heating_active"
ATTR_ZONE_STATUS_VALUE = "zone_status_value"
ATTR_ACTIVE_MODE = "active_mode"
ATTR_MAPPING_COMPLETE = "mapping_complete"
ATTR_ENABLED = "enabled"
ATTR_MANUAL_OVERRIDE_ALLOWED = "manual_override_allowed"
ATTR_CUSTOM_SETPOINTS = "custom_setpoints"
# Attributi nuovi per il raffrescamento estivo
ATTR_CONDITIONING_ACTIVE = "conditioning_active"
ATTR_SEASON = "season"
ATTR_DEW_POINT = "dew_point"
ATTR_SUPPORTS_COOLING = "supports_cooling"
ATTR_SUPPORTS_DEHUMIDIFICATION = "supports_dehumidification"

# Registro globale Season (sola lettura): stagione operativa della centralina.
# Verificato live: mod=10, reg=99, 0=inverno, 1=estate. NON scrivibile.
GLOBAL_SEASON_REGISTER_MOD = 10
GLOBAL_SEASON_REGISTER_REG = 99
GLOBAL_SEASON_VALUE_WINTER = 0
GLOBAL_SEASON_VALUE_SUMMER = 1

# Valori raw del registro per-zona "ZoneN season" (RW, scale 10).
# WINTER=0 / SUMMER=10 come da telegea.conf (THC_SEASON_REG_VAL_*).
ZONE_SEASON_VALUE_WINTER = 0
ZONE_SEASON_VALUE_SUMMER = 10

# Stringhe stagione operativa usate internamente dalla policy/coordinator.
SEASON_WINTER = "winter"
SEASON_SUMMER = "summer"

# Motivi di policy specifici del raffrescamento estivo.
POLICY_REASON_COOLING_NOT_SUPPORTED = "cooling_not_supported"

# Protezione anticondensa: margine di default sopra il punto di rugiada (°C).
DEFAULT_DEWPOINT_MARGIN = 1.5

# Umidita' relativa minima plausibile per un ambiente indoor (%). Letture sotto
# questa soglia (tipicamente un registro mappato male che restituisce un flag
# 0/1) vengono scartate: una RH implausibile produrrebbe un dew point assurdo
# (es. RH=1% -> -33 °C) che disattiva di fatto la protezione anticondensa.
MIN_VALID_HUMIDITY_PCT = 5.0

GLOBAL_MODE_AUTO = "auto"
GLOBAL_MODE_COMFORT = "comfort"
GLOBAL_MODE_ECO = "eco"
GLOBAL_MODE_AWAY = "away"
GLOBAL_MODE_NIGHT = "night"
GLOBAL_MODE_OFF = "off"

GLOBAL_MODES = [
    GLOBAL_MODE_AUTO,
    GLOBAL_MODE_COMFORT,
    GLOBAL_MODE_ECO,
    GLOBAL_MODE_AWAY,
    GLOBAL_MODE_NIGHT,
    GLOBAL_MODE_OFF,
]

SEASON_MODE_AUTO = "auto"
SEASON_MODE_WINTER = "winter"
SEASON_MODE_SUMMER = "summer"

SEASON_MODES = [
    SEASON_MODE_AUTO,
    SEASON_MODE_WINTER,
    SEASON_MODE_SUMMER,
]

WEEKDAY_OPTIONS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
STORAGE_VERSION = 1
