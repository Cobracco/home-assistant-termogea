"""HTTP client for Termogea."""

from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from typing import Any

from aiohttp import ClientError, ClientSession

from .models import RegisterDefinition


class TermogeaApiError(Exception):
    """Base API error."""


class TermogeaAuthError(TermogeaApiError):
    """Raised when authentication fails."""


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

    @property
    def base_url(self) -> str:
        """Return the HTTP base URL."""
        if self._host.startswith("http://") or self._host.startswith("https://"):
            return self._host
        return f"http://{self._host}"

    async def async_force_relogin(self) -> None:
        """Forget the current login state."""
        self._logged_in = False
        self._session.cookie_jar.clear()

    async def async_login(self) -> None:
        """Authenticate against the Termogea login form."""
        async with self._login_lock:
            if self._logged_in:
                return

            try:
                async with self._session.get(
                    f"{self.base_url}/",
                    timeout=self._timeout,
                ):
                    pass

                async with self._session.post(
                    f"{self.base_url}/",
                    data={"username": self._username, "password": self._password},
                    allow_redirects=False,
                    timeout=self._timeout,
                ) as response:
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
                timeout=self._timeout,
            ) as response:
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
