"""HPE OneView REST API client with session management and auto-reauth."""

from __future__ import annotations

import logging
import threading
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class OneViewClient:
    """Thin wrapper around the OneView REST API."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        login_domain: str | None = None,
        api_version: int = 7200,
        ca_bundle: str | bool = True,
    ) -> None:
        self.base_url = f"https://{host}"
        self._username = username
        self._password = password
        self._login_domain = login_domain
        self._api_version = api_version
        self._session_id: str | None = None
        self._lock = threading.Lock()

        self._http = requests.Session()
        self._http.verify = ca_bundle
        retry = Retry(total=2, backoff_factor=0.5, status_forcelist=[502, 503, 504])
        self._http.mount("https://", HTTPAdapter(max_retries=retry))

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def login(self) -> None:
        """Obtain a new session token from OneView."""
        body: dict[str, str] = {
            "userName": self._username,
            "password": self._password,
        }
        if self._login_domain:
            body["authLoginDomain"] = self._login_domain

        resp = self._http.post(
            f"{self.base_url}/rest/login-sessions",
            json=body,
            headers={
                "Content-Type": "application/json",
                "X-Api-Version": str(self._api_version),
            },
            timeout=30,
        )
        resp.raise_for_status()
        self._session_id = resp.json()["sessionID"]
        logger.info("Authenticated with OneView at %s", self.base_url)

    def logout(self) -> None:
        if self._session_id:
            try:
                self._http.delete(
                    f"{self.base_url}/rest/login-sessions",
                    headers=self._auth_headers(),
                    timeout=10,
                )
            except Exception:
                pass
            self._session_id = None

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Auth": self._session_id or "",
            "X-Api-Version": str(self._api_version),
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Generic request with auto-reauth
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET with automatic re-login on 401."""
        if not self._session_id:
            with self._lock:
                if not self._session_id:
                    self.login()

        url = f"{self.base_url}{path}"
        resp = self._http.get(url, headers=self._auth_headers(), params=params, timeout=60)

        if resp.status_code == 401:
            logger.warning("Session expired, re-authenticating...")
            with self._lock:
                self.login()
            resp = self._http.get(url, headers=self._auth_headers(), params=params, timeout=60)

        resp.raise_for_status()
        return resp.json()

    def _get_all(self, path: str, params: dict[str, Any] | None = None) -> list[dict]:
        """Paginate through a collection endpoint and return all members.

        Returns an empty list if the endpoint does not exist (404) on this
        appliance — e.g. /rest/enclosures on rack-only environments.
        """
        params = dict(params or {})
        params.setdefault("start", 0)
        params.setdefault("count", 500)

        all_members: list[dict] = []
        while True:
            try:
                data = self._get(path, params)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    logger.info("Endpoint %s not available (404), skipping", path)
                    return []
                raise
            members = data.get("members", [])
            all_members.extend(members)
            total = data.get("total", len(all_members))
            if len(all_members) >= total:
                break
            params["start"] = len(all_members)
        return all_members

    # ------------------------------------------------------------------
    # Resource endpoints
    # ------------------------------------------------------------------

    def get_server_hardware(self) -> list[dict]:
        return self._get_all("/rest/server-hardware")

    def get_server_utilization(self, server_uri: str) -> dict:
        return self._get(
            f"{server_uri}/utilization",
            params={"fields": "AmbientTemperature,AveragePower,PeakPower,CpuUtilization"},
        )

    def get_enclosures(self) -> list[dict]:
        return self._get_all("/rest/enclosures")

    def get_enclosure_utilization(self, enclosure_uri: str) -> dict:
        return self._get(
            f"{enclosure_uri}/utilization",
            params={"fields": "AmbientTemperature,AveragePower,PeakPower"},
        )

    def get_interconnects(self) -> list[dict]:
        return self._get_all("/rest/interconnects")

    def get_active_alerts(self) -> list[dict]:
        return self._get_all("/rest/alerts", params={"filter": "alertState='Active'"})
