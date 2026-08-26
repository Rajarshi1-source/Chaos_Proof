"""Alertmanager client. API **v2 only** — v1 was removed in 0.27.

Alertmanager is authoritative for what was actually *notified*, which is what the
alert-validation check claims to test. Prometheus's ALERTS{alertstate="firing"}
is an acceptable secondary source but says nothing about notification."""

import datetime as dt

import requests


class AlertmanagerClient:

    def __init__(self, base_url: str, timeout_s: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def alerts(self, alertname: str | None = None) -> list[dict]:
        params = {"active": "true", "silenced": "false", "inhibited": "false"}
        if alertname:
            params["filter"] = f'alertname="{alertname}"'
        r = requests.get(f"{self.base_url}/api/v2/alerts", params=params,
                         timeout=self.timeout_s)
        r.raise_for_status()
        return r.json()

    def first_fired_at(self, alertname: str, since_epoch: float) -> float | None:
        """Earliest startsAt at-or-after `since_epoch`, as an epoch float.
        None when the alert never fired in the window — which is a FAILED check,
        never a silently passing one."""
        best: float | None = None
        for a in self.alerts(alertname):
            started = a.get("startsAt")
            if not started:
                continue
            ts = dt.datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp()
            if ts >= since_epoch - 1.0 and (best is None or ts < best):
                best = ts
        return best
