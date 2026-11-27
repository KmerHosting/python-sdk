"""Official synchronous KmerHosting API client."""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Mapping, Optional


Json = Mapping[str, Any]


class KmerHostingError(RuntimeError):
    """A non-successful response from the KmerHosting API."""

    def __init__(self, message: str, status: int, code: str, request_id: Optional[str] = None, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.request_id = request_id
        self.body = body


class RateLimitError(KmerHostingError):
    """HTTP 429. ``retry_after`` is the server-suggested wait in seconds, when sent."""

    def __init__(
        self,
        message: str,
        code: str,
        request_id: Optional[str] = None,
        body: Any = None,
        retry_after: Optional[float] = None,
        limit: Optional[int] = None,
        remaining: Optional[int] = None,
    ) -> None:
        super().__init__(message, 429, code, request_id, body)
        self.retry_after = retry_after
        self.limit = limit
        self.remaining = remaining


_RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        seconds = float(value)
        return seconds if seconds >= 0 else None
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _parse_int(value: Optional[str]) -> Optional[int]:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def _encode(value: str) -> str:
    return urllib.parse.quote(value, safe="")


class _Resource:
    def __init__(self, client: "KmerHostingClient") -> None:
        self._client = client


class _AccountResource(_Resource):
    def get(self) -> Json:
        return self._client._request("GET", "/v1/account")

    def api_usage(self) -> Json:
        return self._client._request("GET", "/v1/account/api-usage")


class _ServicesResource(_Resource):
    def list(self) -> Json:
        return self._client._request("GET", "/v1/services")

    def get(self, service_id: str) -> Json:
        return self._client._request("GET", f"/v1/services/{_encode(service_id)}")


class _DnsResource(_Resource):
    def list(self, domain_id: str) -> Json:
        return self._client._request("GET", f"/v1/domains/{_encode(domain_id)}/dns")

    def create(self, domain_id: str, record: Json, idempotency_key: Optional[str] = None) -> Json:
        return self._client._mutate("POST", f"/v1/domains/{_encode(domain_id)}/dns", record, idempotency_key)

    def update(self, domain_id: str, record_id: str, record: Json, idempotency_key: Optional[str] = None) -> Json:
        return self._client._mutate("PUT", f"/v1/domains/{_encode(domain_id)}/dns/{_encode(record_id)}", record, idempotency_key)

    def delete(self, domain_id: str, record_id: str, idempotency_key: Optional[str] = None) -> Json:
        return self._client._mutate("DELETE", f"/v1/domains/{_encode(domain_id)}/dns/{_encode(record_id)}", None, idempotency_key)


class _DomainsResource(_Resource):
    def __init__(self, client: "KmerHostingClient") -> None:
        super().__init__(client)
        self.dns = _DnsResource(client)

    def list(self) -> Json:
        return self._client._request("GET", "/v1/domains")

    def get(self, domain_id: str) -> Json:
        return self._client._request("GET", f"/v1/domains/{_encode(domain_id)}")

    def set_auto_renew(self, domain_id: str, enabled: bool, idempotency_key: Optional[str] = None) -> Json:
        return self._client._mutate("PUT", f"/v1/domains/{_encode(domain_id)}/auto-renew", {"enabled": enabled}, idempotency_key)

    def set_nameservers(self, domain_id: str, name_servers: list[str], idempotency_key: Optional[str] = None) -> Json:
        return self._client._mutate("PUT", f"/v1/domains/{_encode(domain_id)}/nameservers", {"nameServers": name_servers}, idempotency_key)

    def search(self, domains: "str | list[str]") -> Json:
        """Check availability and price of 1 to 20 domains.

        Public endpoint on domain.kmerhosting.com: no API key is sent. Limited to 20 requests
        per IP per minute (a bulk search counts once). Returns {"results": [...]}.
        """
        items = [domains] if isinstance(domains, str) else list(domains)
        items = [item.strip() for item in items if item and item.strip()]
        if not 1 <= len(items) <= 20:
            raise ValueError("Pass between 1 and 20 domains to domains.search.")
        query = urllib.parse.urlencode({"domains": ",".join(items)})
        return self._client._request("GET", f"/api/domain-search?{query}", base_url=self._client.domain_search_url, authenticated=False)


class _EmailResource(_Resource):
    def list_services(self) -> Json:
        return self._client._request("GET", "/v1/email/services")

    def provision(self, service_id: str, idempotency_key: Optional[str] = None) -> Json:
        return self._client._mutate("POST", f"/v1/email/services/{_encode(service_id)}/provision", {}, idempotency_key)

    def sync_dns(self, service_id: str, idempotency_key: Optional[str] = None) -> Json:
        return self._client._mutate("POST", f"/v1/email/services/{_encode(service_id)}/dns/sync", {}, idempotency_key)


class _HostingResource(_Resource):
    def list_services(self) -> Json:
        return self._client._request("GET", "/v1/hosting/services")

    def stats(self, service_id: str) -> Json:
        return self._client._request("GET", f"/v1/hosting/services/{_encode(service_id)}/stats")

    def create_panel_access(self, service_id: str, target: str = "panel", idempotency_key: Optional[str] = None) -> Json:
        return self._client._mutate("POST", f"/v1/hosting/services/{_encode(service_id)}/panel-access", {"target": target}, idempotency_key)


class _KvmSnapshotsResource(_Resource):
    def list(self, service_id: str) -> Json:
        return self._client._request("GET", f"/v1/kvm/instances/{_encode(service_id)}/snapshots")

    def create(self, service_id: str, name: str, description: Optional[str] = None, idempotency_key: Optional[str] = None) -> Json:
        body: dict[str, Any] = {"name": name}
        if description is not None:
            body["description"] = description
        return self._client._mutate("POST", f"/v1/kvm/instances/{_encode(service_id)}/snapshots", body, idempotency_key)

    def update(self, service_id: str, snapshot_id: str, name: Optional[str] = None, description: Optional[str] = None, idempotency_key: Optional[str] = None) -> Json:
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        return self._client._mutate("PATCH", f"/v1/kvm/instances/{_encode(service_id)}/snapshots/{_encode(snapshot_id)}", body, idempotency_key)

    def delete(self, service_id: str, snapshot_id: str, idempotency_key: Optional[str] = None) -> Json:
        return self._client._mutate("DELETE", f"/v1/kvm/instances/{_encode(service_id)}/snapshots/{_encode(snapshot_id)}", None, idempotency_key)

    def rollback(self, service_id: str, snapshot_id: str, idempotency_key: Optional[str] = None) -> Json:
        return self._client._mutate("POST", f"/v1/kvm/instances/{_encode(service_id)}/snapshots/rollback", {"snapshotId": snapshot_id}, idempotency_key)


class _LxcResource(_Resource):
    def list(self) -> Json:
        return self._client._request("GET", "/v1/lxc/instances")

    def get(self, service_id: str) -> Json:
        return self._client._request("GET", f"/v1/lxc/instances/{_encode(service_id)}")

    def metrics(self, service_id: str) -> Json:
        return self._client._request("GET", f"/v1/lxc/instances/{_encode(service_id)}/metrics")

    def action(self, service_id: str, action: str, idempotency_key: Optional[str] = None) -> Json:
        return self._client._mutate("POST", f"/v1/lxc/instances/{_encode(service_id)}/actions", {"action": action}, idempotency_key)

    def list_snapshots(self, service_id: str) -> Json:
        return self._client._request("GET", f"/v1/lxc/instances/{_encode(service_id)}/snapshots")

    def snapshot(self, service_id: str, action: str, name: str, idempotency_key: Optional[str] = None) -> Json:
        return self._client._mutate("POST", f"/v1/lxc/instances/{_encode(service_id)}/snapshots", {"action": action, "name": name}, idempotency_key)

    def change_password(self, service_id: str, password: str, idempotency_key: Optional[str] = None) -> Json:
        return self._client._mutate("POST", f"/v1/lxc/instances/{_encode(service_id)}/password", {"password": password}, idempotency_key)

    def reinstall(self, service_id: str, distribution: str, idempotency_key: Optional[str] = None) -> Json:
        return self._client._mutate("POST", f"/v1/lxc/instances/{_encode(service_id)}/reinstall", {"distribution": distribution}, idempotency_key)

    def create_terminal_ticket(self, service_id: str, idempotency_key: Optional[str] = None) -> Json:
        return self._client._mutate("POST", f"/v1/lxc/instances/{_encode(service_id)}/terminal-ticket", {}, idempotency_key)

    def set_auto_renew(self, service_id: str, enabled: bool, idempotency_key: Optional[str] = None) -> Json:
        return self._client._mutate("PUT", f"/v1/lxc/instances/{_encode(service_id)}/auto-renew", {"enabled": enabled}, idempotency_key)

    def set_billing_period(self, service_id: str, billing_months: int, idempotency_key: Optional[str] = None) -> Json:
        return self._client._mutate("PUT", f"/v1/lxc/instances/{_encode(service_id)}/billing-period", {"billingMonths": billing_months}, idempotency_key)


class _KvmResource(_Resource):
    def __init__(self, client: "KmerHostingClient") -> None:
        super().__init__(client)
        self.snapshots = _KvmSnapshotsResource(client)

    def list(self) -> Json:
        return self._client._request("GET", "/v1/kvm/instances")

    def get(self, service_id: str) -> Json:
        return self._client._request("GET", f"/v1/kvm/instances/{_encode(service_id)}")

    def action(self, service_id: str, action: str, idempotency_key: Optional[str] = None) -> Json:
        return self._client._mutate("POST", f"/v1/kvm/instances/{_encode(service_id)}/actions", {"action": action}, idempotency_key)

    def set_auto_renew(self, service_id: str, enabled: bool, idempotency_key: Optional[str] = None) -> Json:
        return self._client._mutate("PUT", f"/v1/kvm/instances/{_encode(service_id)}/auto-renew", {"enabled": enabled}, idempotency_key)

    def reset_password(self, service_id: str, password: str, idempotency_key: Optional[str] = None) -> Json:
        return self._client._mutate("POST", f"/v1/kvm/instances/{_encode(service_id)}/password", {"password": password}, idempotency_key)

    def renew(self, service_id: str, billing_months: Optional[int] = None, idempotency_key: Optional[str] = None) -> Json:
        body = {"billingMonths": billing_months} if billing_months is not None else {}
        return self._client._mutate("POST", f"/v1/kvm/instances/{_encode(service_id)}/renew", body, idempotency_key)

    def cancel(self, service_id: str, idempotency_key: Optional[str] = None) -> Json:
        return self._client._mutate("POST", f"/v1/kvm/instances/{_encode(service_id)}/cancel", {}, idempotency_key)

    def keep_service(self, service_id: str, idempotency_key: Optional[str] = None) -> Json:
        return self._client._mutate("POST", f"/v1/kvm/instances/{_encode(service_id)}/keep-service", {}, idempotency_key)


class KmerHostingClient:
    """A synchronous client. It never stores or logs API credentials."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.kmerhosting.com",
        timeout: float = 30.0,
        domain_search_url: str = "https://domain.kmerhosting.com",
        max_attempts: int = 3,
        retry_base_delay: float = 0.5,
        retry_max_delay: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """``max_attempts`` is the total number of tries for 429/502/503/504 (1 disables retries)."""
        self.api_key = api_key or os.environ.get("KMERHOSTING_API_KEY")
        if not self.api_key:
            raise ValueError("Set KMERHOSTING_API_KEY or pass api_key to KmerHostingClient.")
        self.base_url = base_url.rstrip("/")
        self.domain_search_url = domain_search_url.rstrip("/")
        self.timeout = timeout
        self.max_attempts = max(1, int(max_attempts))
        self.retry_base_delay = retry_base_delay
        self.retry_max_delay = retry_max_delay
        self._sleep = sleep
        self.account = _AccountResource(self)
        self.services = _ServicesResource(self)
        self.domains = _DomainsResource(self)
        self.email = _EmailResource(self)
        self.hosting = _HostingResource(self)
        self.lxc = _LxcResource(self)
        self.kvm = _KvmResource(self)

    def _mutate(self, method: str, path: str, body: Optional[Mapping[str, Any]], idempotency_key: Optional[str]) -> Json:
        return self._request(method, path, body, idempotency_key or str(uuid.uuid4()))

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        base_url: Optional[str] = None,
        authenticated: bool = True,
    ) -> Json:
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        url = f"{base_url or self.base_url}{path}"
        attempt = 1
        while True:
            # A fresh Request per attempt, but the same headers (including Idempotency-Key).
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return self._decode(response.read())
            except urllib.error.HTTPError as error:
                payload = self._decode(error.read())
                if error.code in _RETRYABLE_STATUSES and attempt < self.max_attempts:
                    self._sleep(self._retry_delay(attempt, _parse_retry_after(error.headers.get("Retry-After"))))
                    attempt += 1
                    continue
                raise self._error_for(error, payload) from error
            except urllib.error.URLError as error:
                raise KmerHostingError("Unable to reach the KmerHosting API.", 0, "connection_failed") from error

    def _retry_delay(self, attempt: int, retry_after: Optional[float]) -> float:
        if retry_after is not None:
            delay = retry_after + random.uniform(0, 0.25)
        else:
            delay = random.uniform(0, min(self.retry_max_delay, self.retry_base_delay * 2 ** (attempt - 1)))
        return min(self.retry_max_delay, delay)

    @staticmethod
    def _error_for(error: urllib.error.HTTPError, payload: Any) -> KmerHostingError:
        body = payload if isinstance(payload, Mapping) else {}
        details = body.get("error", {})
        if not isinstance(details, Mapping):
            # The public domain-search service answers {"error": "<code>", "message": "..."}.
            details = {"code": details, "message": body.get("message")}
        message = str(details.get("message") or f"KmerHosting API request failed with status {error.code}.")
        request_id = details.get("request_id") or error.headers.get("X-Request-Id")
        if error.code == 429:
            return RateLimitError(
                str(details.get("message") or "KmerHosting API rate limit exceeded."),
                str(details.get("code") or "rate_limit_exceeded"),
                request_id,
                payload,
                _parse_retry_after(error.headers.get("Retry-After")),
                _parse_int(error.headers.get("X-RateLimit-Limit")),
                _parse_int(error.headers.get("X-RateLimit-Remaining")),
            )
        return KmerHostingError(message, error.code, str(details.get("code") or "request_failed"), request_id, payload)

    @staticmethod
    def _decode(raw: bytes) -> Json:
        if not raw:
            return {}
        text = raw.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"data": text}
        return parsed if isinstance(parsed, Mapping) else {"data": parsed}
