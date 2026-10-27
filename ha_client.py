"""Home-Assistant-REST-Client, IP-Erkennung und Tracker-Zuordnung.

Unterstuetzte Abläufe
---------------------
* ``GET /api/states`` lesen und ``device_tracker``-Entities finden, deren
  ``ip``/``ip_address``/``current_ip``-Attribut zur Client-IP passt.
* Anwesenheit anhand von ``state == "home"`` aufloesen.
* ``custom_user_id`` in den Tracker-State schreiben. Da Home Assistant
  Attribute von Entitaeten nicht ueber die generische REST-API setzen kann,
  ruft der Client einen konfigurierbaren Script-Service auf
  (Default: ``script.link_tracker_user``). Die passende configuration.yaml
  inklusive python_script steht in der README.md.
"""

from __future__ import annotations

import ipaddress
from typing import Any

import httpx


class HAError(RuntimeError):
    """Fehler bei der Kommunikation mit Home Assistant."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


#: Attributnamen, unter denen Tracker-Integrationen die IP ablegen.
IP_ATTRIBUTE_KEYS = ("ip", "ip_address", "current_ip", "last_ip")


def normalize_ip(raw: Any) -> str:
    """Vereinheitlicht IPv4/IPv6 (inkl. ``[::1]:1234`` und IPv4-Mapped IPv6)."""
    if raw is None:
        return ""
    value = str(raw).strip().strip('"')
    if not value:
        return ""
    if value.startswith("["):
        # [2001:db8::1]:443
        value = value.split("]", 1)[0].lstrip("[")
    if value.count(":") == 1 and "." in value:
        # 192.0.2.10:51234
        value = value.split(":", 1)[0]
    value = value.split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return value
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return str(ip.ipv4_mapped)
    return ip.compressed


def extract_client_ip(request: Any, trust_forwarded_for: bool = True) -> str:
    """Liest die Client-IP, inklusive Reverse-Proxy-Headern.

    Reihenfolge bei ``trust_forwarded_for``: ``X-Forwarded-For`` (erster
    Eintrag = Original-Client), ``X-Real-IP``, ``Forwarded``, Socket-Peer.
    """
    if trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            first = forwarded.split(",", 1)[0].strip()
            if first:
                return normalize_ip(first)
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return normalize_ip(real_ip)
        forwarded_header = request.headers.get("forwarded")
        if forwarded_header:
            for part in forwarded_header.split(";"):
                key, _, value = part.partition("=")
                if key.strip().lower() == "for" and value:
                    return normalize_ip(value)
    client = getattr(request, "client", None)
    return normalize_ip(getattr(client, "host", "")) if client else ""


class HAClient:
    """Async-Client fuer die Home-Assistant-REST-API."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 8.0,
        verify_ssl: bool = True,
        link_service: str = "script.link_tracker_user",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.link_service = link_service
        self._http: httpx.AsyncClient | None = None
        self._transport = transport
        self._verify_ssl = verify_ssl

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(self.timeout),
                verify=self._verify_ssl,
                transport=self._transport,
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()

    async def _request(
        self, method: str, path: str, *, json_body: dict[str, Any] | None = None
    ) -> Any:
        if not self.configured:
            raise HAError("Home Assistant ist nicht konfiguriert (HA_BASE_URL/HA_TOKEN).")
        client = await self._client()
        try:
            response = await client.request(method, path, json=json_body)
        except httpx.HTTPError as exc:
            raise HAError(f"Home Assistant nicht erreichbar: {exc}") from exc
        if response.status_code == 401:
            raise HAError("Home Assistant lehnt den Token ab (401).", 401)
        if response.status_code == 404:
            raise HAError(f"Home-Assistant-Endpunkt nicht gefunden: {path}", 404)
        if response.status_code >= 400:
            raise HAError(
                f"Home Assistant antwortet mit {response.status_code}: "
                f"{response.text[:200]}",
                response.status_code,
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise HAError("Home Assistant lieferte ungueltiges JSON.") from exc

    async def states(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/api/states")
        if not isinstance(data, list):
            raise HAError("Unerwartete Antwort von /api/states.")
        return [state for state in data if isinstance(state, dict)]

    async def get_state(self, entity_id: str) -> dict[str, Any] | None:
        try:
            data = await self._request("GET", f"/api/states/{entity_id}")
        except HAError as exc:
            if exc.status_code == 404:
                return None
            raise
        return data if isinstance(data, dict) else None

    @staticmethod
    def tracker_ip(tracker: dict[str, Any]) -> str:
        attributes = tracker.get("attributes") or {}
        for key in IP_ATTRIBUTE_KEYS:
            if attributes.get(key):
                return normalize_ip(attributes[key])
        return ""

    @classmethod
    def find_tracker_by_ip(
        cls, states: list[dict[str, Any]], ip: str
    ) -> dict[str, Any] | None:
        """Findet den ``device_tracker``, dessen IP-Attribut zur IP passt."""
        wanted = normalize_ip(ip)
        if not wanted:
            return None
        for state in states:
            if not str(state.get("entity_id", "")).startswith("device_tracker."):
                continue
            if cls.tracker_ip(state) == wanted:
                return state
        return None

    @classmethod
    def match_user_tracker(
        cls, states: list[dict[str, Any]], user: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Ordnet einen registrierten Nutzer einem Tracker zu.

        Prioritaet: gespeicherte entity_id, ``custom_user_id``-Attribut,
        IP-Attribut (live), zuletzt die beim Onboarding gespeicherte IP.
        """
        trackers = [
            state
            for state in states
            if str(state.get("entity_id", "")).startswith("device_tracker.")
        ]
        if user.get("ha_entity_id"):
            for tracker in trackers:
                if tracker.get("entity_id") == user["ha_entity_id"]:
                    return tracker
        custom_user_id = user.get("custom_user_id")
        if custom_user_id:
            for tracker in trackers:
                attributes = tracker.get("attributes") or {}
                if attributes.get("custom_user_id") == custom_user_id:
                    return tracker
        for candidate_ip in (user.get("ip"),):
            tracker = cls.find_tracker_by_ip(trackers, candidate_ip or "")
            if tracker:
                return tracker
        return None

    async def link_custom_user(self, entity_id: str, custom_user_id: str) -> str:
        """Schreibt ``custom_user_id`` ueber den konfigurierten Script-Service."""
        if not entity_id or not custom_user_id:
            raise HAError("entity_id und custom_user_id sind erforderlich.")
        domain, separator, service = self.link_service.partition(".")
        if not separator or not domain or not service:
            raise HAError(
                f"HA_LINK_SERVICE muss die Form 'domain.service' haben, ist aber "
                f"'{self.link_service}'."
            )
        await self._request(
            "POST",
            f"/api/services/{domain}/{service}",
            json_body={"entity_id": entity_id, "custom_user_id": custom_user_id},
        )
        return self.link_service

    @staticmethod
    def tracker_is_home(tracker: dict[str, Any] | None) -> bool:
        return bool(tracker) and str(tracker.get("state", "")).lower() == "home"
