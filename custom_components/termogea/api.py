"""HTTP client for Termogea."""

from __future__ import annotations

import asyncio
import configparser
import io
import html
import json
import re
import tarfile
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any
from urllib.parse import quote

from aiohttp import ClientError, ClientSession

from .models import GlobalConfig, RegisterDefinition, ScheduleRule, ZoneDefinition


class TermogeaApiError(Exception):
    """Base API error."""


class TermogeaAuthError(TermogeaApiError):
    """Raised when authentication fails."""


WEEKDAY_MAP: Mapping[str, str] = {
    "monday": "mon",
    "tuesday": "tue",
    "wednesday": "wed",
    "thursday": "thu",
    "friday": "fri",
    "saturday": "sat",
    "sunday": "sun",
}


class TermogeaClient:
    """Client for the Termogea local web UI."""

    def __init__(
        self,
        session: ClientSession,
        host: str,
        username: str,
        password: str,
        timeout: int,
    ) -> None:
        self._session = session
        self._host = host.rstrip("/")
        self._username = username
        self._password = password
        self._timeout = timeout
        self._login_lock = asyncio.Lock()
        self._logged_in = False
        self._php_session_id: str | None = None

    @property
    def base_url(self) -> str:
        """Return the HTTP base URL."""
        if self._host.startswith("http://") or self._host.startswith("https://"):
            return self._host
        return f"http://{self._host}"

    async def async_force_relogin(self) -> None:
        """Forget the current login state."""
        self._logged_in = False
        self._php_session_id = None
        self._session.cookie_jar.clear()

    def _request_headers(self) -> dict[str, str] | None:
        if self._php_session_id:
            return {"Cookie": f"PHPSESSID={self._php_session_id}"}
        return None

    def _store_php_session(self, response) -> None:
        cookie = response.cookies.get("PHPSESSID")
        if cookie is not None and cookie.value:
            self._php_session_id = cookie.value

    @staticmethod
    def _strip_quotes(value: str | None) -> str:
        if value is None:
            return ""
        return value.strip().strip("'").strip('"').strip()

    @staticmethod
    def _safe_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError, AttributeError):
            return None

    @staticmethod
    def _precision_from_divisor(divisor: float) -> int:
        if divisor <= 0:
            return 1
        precision = 0
        scaled = divisor
        while scaled >= 10 and abs(scaled - round(scaled)) < 1e-9 and int(round(scaled)) % 10 == 0:
            precision += 1
            scaled /= 10
        if precision == 0 and abs(divisor - 1.0) > 1e-9:
            precision = 1
        return precision

    @staticmethod
    def _mode_from_temp(temp: float, comfort: float, eco: float, away: float) -> str:
        if temp >= comfort - 0.1:
            return "comfort"
        if temp >= eco - 0.1:
            return "eco"
        if temp <= away + 0.1:
            return "away"
        return "night"

    @staticmethod
    def _first_non_empty_option(section: Mapping[str, Any], keys: tuple[str, ...]) -> str:
        for key in keys:
            value = TermogeaClient._strip_quotes(section.get(key))
            if value:
                return value
        return ""

    @staticmethod
    def _find_humidity_reg_name_in_section(section: Mapping[str, Any]) -> str:
        """Extract humidity register name from arbitrary config keys."""
        for key, value in section.items():
            upper_key = str(key).upper()
            if "REG_NAME" not in upper_key:
                continue
            if re.search(r"(HUM|UMID|RH|HNOW|UR)", upper_key):
                register_name = TermogeaClient._strip_quotes(str(value))
                if register_name:
                    return register_name
        return ""

    @staticmethod
    def _find_register_by_names(
        register_catalog: dict[str, tuple[RegisterDefinition, str]],
        names: list[str],
    ) -> RegisterDefinition | None:
        """Return first matching register definition by case-insensitive name."""
        if not names:
            return None
        lowered_catalog = {name.lower(): definition for name, (definition, _mode) in register_catalog.items()}
        for candidate in names:
            if not candidate:
                continue
            found = lowered_catalog.get(candidate.lower())
            if found is not None:
                return found
        return None

    @staticmethod
    def _humidity_name_candidates_from_temperature_name(
        tnow_name: str,
        zone_index: int,
    ) -> list[str]:
        """Generate likely humidity register names from the temperature register name."""
        if not tnow_name:
            return []

        candidates: list[str] = []
        substitutions = (
            ("TNOW", "HNOW"),
            ("TNOW", "RHNOW"),
            ("TNOW", "HUM"),
            ("TNOW", "UMID"),
            ("TEMP", "HUM"),
            ("TEMP", "RH"),
        )
        for source, target in substitutions:
            if source in tnow_name.upper():
                candidate = re.sub(source, target, tnow_name, flags=re.IGNORECASE)
                if candidate and candidate not in candidates:
                    candidates.append(candidate)

        zone_suffixes = (
            f"HNOW_{zone_index}",
            f"RHNOW_{zone_index}",
            f"HUM_{zone_index}",
            f"UMID_{zone_index}",
            f"HNOW{zone_index}",
            f"RHNOW{zone_index}",
            f"HUM{zone_index}",
            f"UMID{zone_index}",
        )
        for suffix in zone_suffixes:
            if suffix not in candidates:
                candidates.append(suffix)
        return candidates

    @staticmethod
    def _guess_zone_humidity_register(
        register_catalog: dict[str, tuple[RegisterDefinition, str]],
        zone_index: int,
    ) -> RegisterDefinition | None:
        """Best-effort lookup for zone humidity register when config key is missing."""
        best_score = -1
        best: RegisterDefinition | None = None

        for name, (definition, mode) in register_catalog.items():
            lower = name.lower()
            humidity_score = 0
            if re.search(r"(humidity|humid|umid|hnow|rhnow)", lower):
                humidity_score += 5
            if re.search(r"\brh\b", lower):
                humidity_score += 4
            if re.search(r"\bur\b|\burh\b", lower):
                humidity_score += 2
            if humidity_score == 0:
                continue

            zone_score = 0
            if re.search(rf"(zone|zona)\s*0*{zone_index}\b", lower):
                zone_score = 4
            elif re.search(rf"\bz\s*0*{zone_index}\b", lower):
                zone_score = 3
            elif re.search(rf"[_\-\s]0*{zone_index}\b", lower):
                zone_score = 1

            mode_score = 2 if "R" in mode else 0
            score = humidity_score + zone_score + mode_score
            if score > best_score:
                best_score = score
                best = definition

        return best if best_score >= 4 else None

    async def async_login(self) -> None:
        """Authenticate against the Termogea login form."""
        async with self._login_lock:
            if self._logged_in:
                return

            try:
                async with self._session.get(
                    f"{self.base_url}/",
                    headers=self._request_headers(),
                    timeout=self._timeout,
                ) as response:
                    self._store_php_session(response)
                    await response.read()

                async with self._session.post(
                    f"{self.base_url}/",
                    data={"username": self._username, "password": self._password},
                    headers=self._request_headers(),
                    allow_redirects=False,
                    timeout=self._timeout,
                ) as response:
                    self._store_php_session(response)
                    location = response.headers.get("Location", "")
                    body = await response.text()
            except ClientError as err:
                raise TermogeaApiError(f"Unable to reach Termogea host: {err}") from err

            if (
                response.status == HTTPStatus.FOUND
                and "/webgui/tsg/service_mode.php" in location
            ):
                self._logged_in = True
                return

            if "service_mode.php" in body:
                self._logged_in = True
                return

            raise TermogeaAuthError("Invalid Termogea credentials")

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        allow_retry: bool = True,
    ) -> str:
        await self.async_login()

        try:
            async with self._session.request(
                method,
                f"{self.base_url}{path}",
                data=data,
                headers=self._request_headers(),
                timeout=self._timeout,
            ) as response:
                self._store_php_session(response)
                text = await response.text()
        except ClientError as err:
            raise TermogeaApiError(f"HTTP request failed for {path}: {err}") from err

        if (
            response.status in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}
            or "<h5 class=\"white-text" in text
        ):
            await self.async_force_relogin()
            if allow_retry:
                return await self._async_request(
                    method,
                    path,
                    data=data,
                    allow_retry=False,
                )
            raise TermogeaAuthError("Termogea session expired")

        if response.status >= HTTPStatus.BAD_REQUEST:
            raise TermogeaApiError(f"Unexpected status {response.status} for {path}")

        return text

    async def _async_request_bytes(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        allow_retry: bool = True,
    ) -> bytes:
        await self.async_login()

        try:
            async with self._session.request(
                method,
                f"{self.base_url}{path}",
                data=data,
                headers=self._request_headers(),
                timeout=self._timeout,
            ) as response:
                self._store_php_session(response)
                payload = await response.read()
                text = payload.decode(errors="ignore")
        except ClientError as err:
            raise TermogeaApiError(f"HTTP request failed for {path}: {err}") from err

        if (
            response.status in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}
            or "<h5 class=\"white-text" in text
        ):
            await self.async_force_relogin()
            if allow_retry:
                return await self._async_request_bytes(
                    method,
                    path,
                    data=data,
                    allow_retry=False,
                )
            raise TermogeaAuthError("Termogea session expired")

        if response.status >= HTTPStatus.BAD_REQUEST:
            raise TermogeaApiError(f"Unexpected status {response.status} for {path}")

        return payload

    async def async_check_thcontrol_status(self) -> int:
        """Read thcontrol service status."""
        text = await self._async_request(
            "GET",
            "/webgui/api.php?cmd=check_status_service&service=thcontrol",
        )
        try:
            return int(text.strip())
        except ValueError as err:
            raise TermogeaApiError(
                f"Unable to parse thcontrol status: {text!r}"
            ) from err

    async def async_fetch_zone_names(self) -> dict[int, str]:
        """Read configured zone names from setup page tabs."""
        text = await self._async_request("GET", "/webgui/tsg/setup.php?lang=it")
        pattern = re.compile(
            r'<li[^>]*id="tab_zone(?P<idx>\d+)"[^>]*>.*?<a[^>]*>(?P<name>.*?)</a>',
            re.IGNORECASE | re.DOTALL,
        )
        names: dict[int, str] = {}
        for match in pattern.finditer(text):
            idx = int(match.group("idx"))
            raw_name = html.unescape(match.group("name"))
            clean_name = " ".join(raw_name.replace("\xa0", " ").split())
            if clean_name:
                names[idx] = clean_name
        return names

    async def async_download_controller_file(self, remote_path: str) -> bytes:
        """Download one controller-side file through the web GUI endpoint."""
        encoded = quote(remote_path, safe="")
        return await self._async_request_bytes(
            "GET",
            f"/webgui/tcg/download.php?filename={encoded}",
        )

    @staticmethod
    def _load_ini(raw_text: str) -> configparser.ConfigParser:
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read_string(raw_text)
        return parser

    @staticmethod
    def _parse_reg_list(raw_text: str) -> dict[str, tuple[RegisterDefinition, str]]:
        catalog: dict[str, tuple[RegisterDefinition, str]] = {}
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) < 8:
                continue
            _, mod, reg, name, divisor, mode, *_ = parts
            name = name.strip()
            if not name:
                continue
            try:
                mod_int = int(mod)
                reg_int = int(reg)
            except ValueError:
                continue
            scale = TermogeaClient._safe_float(divisor, 1.0) or 1.0
            definition = RegisterDefinition(
                mod=mod_int,
                reg=reg_int,
                scale=scale,
                precision=TermogeaClient._precision_from_divisor(scale),
            )
            catalog[name] = (definition, mode.strip().upper())
        return catalog

    def _parse_schedule_rules(
        self,
        raw_text: str,
        comfort_temp: float,
        eco_temp: float,
        away_temp: float,
    ) -> list[ScheduleRule]:
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            return []

        daily = payload.get("daily_schedule")
        if not isinstance(daily, list):
            return []

        rules: list[ScheduleRule] = []
        rule_num = 1
        for day_info in daily:
            if not isinstance(day_info, dict):
                continue
            weekday_raw = str(day_info.get("weekday", "")).strip().lower()
            day = WEEKDAY_MAP.get(weekday_raw)
            if not day:
                continue
            periods = day_info.get("times_of_operation")
            if not isinstance(periods, list):
                continue
            for block in periods:
                if not isinstance(block, dict):
                    continue
                start = str(block.get("start", "")).strip()
                end = str(block.get("stop", "")).strip()
                if not start or not end:
                    continue
                if end == "24:00":
                    end = "23:59"
                temp = self._safe_float(block.get("temp"), comfort_temp)
                mode = self._mode_from_temp(temp, comfort_temp, eco_temp, away_temp)
                if start == end:
                    continue
                rules.append(
                    ScheduleRule(
                        rule_id=f"import_{day}_{rule_num}",
                        name=f"Import {day.upper()} {rule_num}",
                        days=[day],
                        start=start,
                        end=end,
                        mode=mode,
                    )
                )
                rule_num += 1
        return rules

    async def async_fetch_controller_bootstrap(
        self,
    ) -> tuple[GlobalConfig, list[ZoneDefinition]]:
        """Build initial HA runtime config from Termogea controller files."""
        bundle_bytes = await self.async_download_controller_file("/media/data/config/telegea.tar")
        try:
            archive = tarfile.open(fileobj=io.BytesIO(bundle_bytes))
        except (tarfile.TarError, OSError) as err:
            raise TermogeaApiError(f"Invalid controller configuration archive: {err}") from err

        def _read_member(name: str) -> str | None:
            member = archive.extractfile(name)
            if member is None:
                return None
            return member.read().decode(errors="ignore")

        zone_names_raw = _read_member("zone_names.json")
        reg_list_raw = _read_member("reg_list.txt")
        conf_raw = _read_member("telegea.conf")
        custom_raw = _read_member("telegea_thcontrol_custom.conf")
        archive.close()

        if reg_list_raw is None or conf_raw is None:
            raise TermogeaApiError("Controller archive missing mandatory files (reg_list.txt / telegea.conf)")

        names_by_zone: dict[int, str] = {}
        if zone_names_raw:
            try:
                parsed_names = json.loads(zone_names_raw)
                for item in parsed_names.get("names", []):
                    zone_number = self._safe_int(item.get("zone"))
                    zone_name = str(item.get("name", "")).strip()
                    if zone_number and zone_name:
                        names_by_zone[zone_number] = zone_name
            except (TypeError, ValueError, json.JSONDecodeError):
                names_by_zone = {}

        register_catalog = self._parse_reg_list(reg_list_raw)
        parser = self._load_ini(conf_raw)
        custom_parser = self._load_ini(custom_raw) if custom_raw else configparser.ConfigParser(interpolation=None)
        custom_parser.optionxform = str

        base_global = parser["thcontrol"] if parser.has_section("thcontrol") else {}
        custom_global = custom_parser["thcontrol"] if custom_parser.has_section("thcontrol") else {}

        t_min = self._safe_float(
            custom_global.get("THC_T_MIN", base_global.get("THC_T_MIN", 16.0)),
            16.0,
        )
        t_max = self._safe_float(
            custom_global.get("THC_T_MAX", base_global.get("THC_T_MAX", 30.0)),
            30.0,
        )
        comfort = round((t_min + t_max) / 2, 1)
        eco = round((comfort + t_min) / 2, 1)
        away = round(t_min, 1)
        night = round((comfort + eco) / 2, 1)
        inactive = away

        zones: list[ZoneDefinition] = []
        for idx in range(1, 13):
            section_name = f"thcontrol_zone{idx}"
            if not parser.has_section(section_name):
                continue
            section = parser[section_name]
            custom_section = custom_parser[section_name] if custom_parser.has_section(section_name) else {}

            tnow_name = self._strip_quotes(section.get("THC_TNOW_REG_NAME", ""))
            tset_name = self._strip_quotes(section.get("THC_TSET_REG_NAME", ""))
            onoff_name = self._strip_quotes(section.get("THC_ONOFF_REG_NAME", ""))
            hnow_name = self._first_non_empty_option(
                section,
                (
                    "THC_HNOW_REG_NAME",
                    "THC_HUM_NOW_REG_NAME",
                    "THC_RHNOW_REG_NAME",
                    "THC_HUMIDITY_REG_NAME",
                ),
            )
            if not hnow_name:
                hnow_name = self._find_humidity_reg_name_in_section(section)

            current_def = register_catalog.get(tnow_name, (None, ""))[0] if tnow_name else None
            humidity_def = register_catalog.get(hnow_name, (None, ""))[0] if hnow_name else None
            if humidity_def is None and tnow_name:
                humidity_def = self._find_register_by_names(
                    register_catalog,
                    self._humidity_name_candidates_from_temperature_name(tnow_name, idx),
                )
            if humidity_def is None:
                humidity_def = self._guess_zone_humidity_register(register_catalog, idx)
            target_tuple = register_catalog.get(tset_name) if tset_name else None
            target_def = None
            if target_tuple is not None and "W" in target_tuple[1]:
                target_def = target_tuple[0]

            hvac_def = None
            hvac_tuple = register_catalog.get(onoff_name) if onoff_name else None
            on_val = self._safe_int(section.get("THC_ONOFF_REG_VAL_ON"))
            off_val = self._safe_int(section.get("THC_ONOFF_REG_VAL_OFF"))
            if hvac_tuple is not None and on_val is not None and off_val is not None and "W" in hvac_tuple[1]:
                hvac_def = RegisterDefinition(
                    mod=hvac_tuple[0].mod,
                    reg=hvac_tuple[0].reg,
                    scale=hvac_tuple[0].scale,
                    precision=hvac_tuple[0].precision,
                    off_value=off_val,
                    heat_value=on_val,
                )

            zone_comfort = self._safe_float(
                custom_section.get("THC_D_SETPOINT1", comfort),
                comfort,
            )
            zone_eco = self._safe_float(
                custom_section.get("THC_D_SETPOINT2", eco),
                eco,
            )
            zone_enabled = str(section.get("THC_ZONE_ENABLED", "true")).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }

            zones.append(
                ZoneDefinition(
                    zone_id=f"zona_{idx}",
                    name=names_by_zone.get(idx, f"Zona {idx}"),
                    current_temperature=current_def,
                    current_humidity=humidity_def,
                    target_temperature=target_def,
                    hvac_mode=hvac_def,
                    comfort_temp=zone_comfort,
                    eco_temp=zone_eco,
                    away_temp=away,
                    night_temp=night,
                    inactive_temp=inactive,
                    enabled=zone_enabled,
                )
            )

        rules_winter: list[ScheduleRule] = []
        rules_summer: list[ScheduleRule] = []
        if parser.has_section("thcontrol_zone1"):
            zone1 = parser["thcontrol_zone1"]
            win_path = self._strip_quotes(zone1.get("THC_TPRG_CONF_FILE_WIN", ""))
            sum_path = self._strip_quotes(zone1.get("THC_TPRG_CONF_FILE_SUM", ""))
            if win_path:
                try:
                    win_schedule = await self.async_download_controller_file(win_path)
                    rules_winter = self._parse_schedule_rules(
                        win_schedule.decode(errors="ignore"),
                        comfort_temp=comfort,
                        eco_temp=eco,
                        away_temp=away,
                    )
                except TermogeaApiError:
                    rules_winter = []
            if sum_path:
                try:
                    sum_schedule = await self.async_download_controller_file(sum_path)
                    rules_summer = self._parse_schedule_rules(
                        sum_schedule.decode(errors="ignore"),
                        comfort_temp=comfort,
                        eco_temp=eco,
                        away_temp=away,
                    )
                except TermogeaApiError:
                    rules_summer = []

        global_config = GlobalConfig(
            global_enabled=True,
            automations_enabled=True,
            allow_common_without_people=False,
            season_mode="auto",
            global_mode="auto",
            auto_fallback_mode="eco",
            comfort_temp=comfort,
            eco_temp=eco,
            away_temp=away,
            night_temp=night,
            inactive_temp=inactive,
            winter_comfort_temp=comfort,
            winter_eco_temp=eco,
            winter_away_temp=away,
            winter_night_temp=night,
            winter_inactive_temp=inactive,
            summer_comfort_temp=comfort,
            summer_eco_temp=eco,
            summer_away_temp=away,
            summer_night_temp=night,
            summer_inactive_temp=inactive,
            schedule_enabled=bool(rules_winter or rules_summer),
            schedule_rules=rules_winter or rules_summer,
            schedule_rules_winter=rules_winter,
            schedule_rules_summer=rules_summer,
        )
        return global_config, zones

    async def async_read_register(
        self,
        register: RegisterDefinition,
    ) -> tuple[int | None, float | None]:
        """Read a Termogea register."""
        payload = json.dumps(
            [{"mod": register.mod, "reg": register.reg}],
            separators=(",", ":"),
        )
        text = await self._async_request(
            "POST",
            f"/api/command.php?dev_cmd={payload}",
        )
        try:
            data = json.loads(text)
            raw = data["result"][0]["val"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as err:
            raise TermogeaApiError(
                f"Unable to parse register response for mod={register.mod} reg={register.reg}"
            ) from err

        if raw is None:
            return None, None

        value = round(float(raw) / register.scale, register.precision)
        return int(raw), value

    async def async_write_register_value(
        self,
        register: RegisterDefinition,
        raw_value: int,
    ) -> None:
        """Write a raw register value."""
        payload = json.dumps(
            [{"mod": register.mod, "reg": register.reg, "val": int(raw_value)}],
            separators=(",", ":"),
        )
        await self._async_request("POST", f"/api/command.php?dev_cmd={payload}")

    async def async_write_scaled_register(
        self,
        register: RegisterDefinition,
        value: float,
    ) -> None:
        """Write a scaled register value."""
        raw_value = round(value * register.scale)
        await self.async_write_register_value(register, raw_value)
