"""Constants for the Termogea integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "termogea"
PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
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

ATTR_ZONE_ID = "zone_id"
ATTR_ASSIGNED_PEOPLE = "assigned_people"
ATTR_PRESENCE_SENSOR = "presence_sensor"
ATTR_IS_COMMON_AREA = "is_common_area"
ATTR_POLICY_REASON = "policy_reason"
ATTR_EFFECTIVE_TARGET = "effective_target"
ATTR_ASSIGNED_PEOPLE_PRESENT = "assigned_people_present"
ATTR_PRESENCE_DETECTED = "presence_detected"
ATTR_ZONE_ENABLED = "zone_enabled"
ATTR_ACTIVE_MODE = "active_mode"
ATTR_MAPPING_COMPLETE = "mapping_complete"
ATTR_ENABLED = "enabled"
ATTR_MANUAL_OVERRIDE_ALLOWED = "manual_override_allowed"

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
