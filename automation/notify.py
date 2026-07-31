"""Webhook notifications for operational breaker events and daily digests.

The webhook URL is deliberately read from ``FIN_TRADE_ALERT_WEBHOOK_URL`` at
construction time rather than being kept in project configuration.  This keeps
secrets out of source control and makes notification delivery optional: a
missing URL is a safe no-op.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

import requests

from automation.digest import DailyDigest, render_text

__all__ = ["WebhookNotifier", "send_breaker_alert", "send_digest_alert"]


class _HttpClient(Protocol):
    def post(self, url: str, **kwargs: Any) -> Any: ...


@dataclass
class WebhookNotifier:
    """Send JSON alert payloads to an operator-controlled webhook endpoint."""

    webhook_url: str | None = None
    client: _HttpClient | None = None
    timeout_seconds: float = 5.0

    @classmethod
    def from_env(cls, *, environ: Mapping[str, str] | None = None, client: _HttpClient | None = None) -> "WebhookNotifier":
        """Create a notifier from ``FIN_TRADE_ALERT_WEBHOOK_URL`` only."""
        values = os.environ if environ is None else environ
        return cls(webhook_url=values.get("FIN_TRADE_ALERT_WEBHOOK_URL") or None, client=client)

    @property
    def enabled(self) -> bool:
        """Whether this notifier has a configured destination."""
        return bool(self.webhook_url)

    def send(self, event: str, text: str, *, details: Mapping[str, Any] | None = None) -> bool:
        """Deliver one alert; return ``False`` for disabled or failed delivery.

        Notification failure must never interrupt trading/risk handling, hence
        network exceptions and non-success HTTP statuses are converted to a
        false return value for the caller to log or meter.
        """
        if not self.webhook_url:
            return False
        payload = {"event": event, "text": text, "details": dict(details or {})}
        try:
            response = (self.client or requests).post(self.webhook_url, json=payload, timeout=self.timeout_seconds)
            response.raise_for_status()
        except requests.RequestException:
            return False
        return True


def send_breaker_alert(notifier: WebhookNotifier, *, category: str, level: str, action: str) -> bool:
    """Send a concise circuit-breaker alert through ``notifier``."""
    text = f"Circuit breaker [{level}] {category}: {action}"
    return notifier.send("breaker", text, details={"category": category, "level": level, "action": action})


def send_digest_alert(notifier: WebhookNotifier, digest: DailyDigest) -> bool:
    """Send the existing deterministic daily-digest rendering as an alert."""
    return notifier.send("digest", render_text(digest), details={"date": digest.date})
