"""Small, optional client for running LEAN backtests through QuantConnect Cloud.

The application deliberately does not import the LEAN CLI: local LEAN execution
requires Docker, while the production web service runs on Render's Python runtime.
This client talks to QuantConnect's documented Cloud API and is inert until all
required credentials and a project ID are configured.
"""
from __future__ import annotations

import base64
import hashlib
import os
import time
from dataclasses import dataclass
from typing import Any, Mapping

import requests


BASE_URL = "https://www.quantconnect.com/api/v2"


class QuantConnectError(RuntimeError):
    """Raised when QuantConnect rejects a request or returns an invalid response."""


@dataclass(frozen=True)
class QuantConnectConfig:
    user_id: str
    api_token: str
    project_id: int
    base_url: str = BASE_URL
    request_timeout: float = 20.0

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None):
        env = os.environ if environ is None else environ
        user_id = str(env.get("QC_USER_ID", "")).strip()
        api_token = str(env.get("QC_API_TOKEN", "")).strip()
        project_id_raw = str(env.get("QC_PROJECT_ID", "")).strip()
        if not (user_id and api_token and project_id_raw):
            return None
        try:
            project_id = int(project_id_raw)
        except ValueError as exc:
            raise ValueError("QC_PROJECT_ID must be an integer") from exc
        if project_id <= 0:
            raise ValueError("QC_PROJECT_ID must be positive")
        return cls(
            user_id=user_id,
            api_token=api_token,
            project_id=project_id,
            base_url=str(env.get("QC_API_BASE_URL", BASE_URL)).strip().rstrip("/"),
        )


class QuantConnectCloud:
    """Authenticated, bounded client for the QuantConnect Cloud backtest API."""

    def __init__(self, config: QuantConnectConfig, session=None, clock=None):
        self.config = config
        self.session = session or requests.Session()
        self._clock = clock or time.time

    def _headers(self) -> dict[str, str]:
        timestamp = str(int(self._clock()))
        token = f"{self.config.api_token}:{timestamp}".encode("utf-8")
        digest = hashlib.sha256(token).hexdigest()
        credentials = f"{self.config.user_id}:{digest}".encode("utf-8")
        authorization = base64.b64encode(credentials).decode("ascii")
        return {"Authorization": f"Basic {authorization}", "Timestamp": timestamp}

    def _post(self, endpoint: str, payload: Mapping[str, Any] | None = None):
        url = f"{self.config.base_url}/{endpoint.lstrip('/')}"
        try:
            response = self.session.post(
                url,
                headers=self._headers(),
                json=dict(payload or {}),
                timeout=self.config.request_timeout,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            # Never include request headers or credentials in exception text.
            raise QuantConnectError(
                f"QuantConnect request failed ({endpoint}): {type(exc).__name__}"
            ) from exc
        except ValueError as exc:
            raise QuantConnectError(
                f"QuantConnect returned invalid JSON ({endpoint})"
            ) from exc
        if not isinstance(data, dict):
            raise QuantConnectError(f"Unexpected QuantConnect response ({endpoint})")
        if data.get("success") is False:
            errors = data.get("errors") or ["request rejected"]
            safe_errors = "; ".join(str(item)[:240] for item in errors[:5])
            raise QuantConnectError(f"QuantConnect rejected {endpoint}: {safe_errors}")
        return data

    def authenticate(self) -> dict[str, Any]:
        result = self._post("authenticate")
        if result.get("success") is not True:
            raise QuantConnectError("QuantConnect authentication was not confirmed")
        return {"authenticated": True}

    def create_compile(self) -> str:
        result = self._post("compile/create", {"projectId": self.config.project_id})
        compile_id = result.get("compileId")
        if not compile_id:
            raise QuantConnectError("QuantConnect did not return a compile ID")
        return str(compile_id)

    def read_compile(self, compile_id: str) -> dict[str, Any]:
        result = self._post(
            "compile/read",
            {"projectId": self.config.project_id, "compileId": str(compile_id)},
        )
        return result.get("compile") or result

    def create_backtest(
        self, compile_id: str, backtest_name: str, parameters=None
    ) -> str:
        payload = {
            "projectId": self.config.project_id,
            "compileId": str(compile_id),
            "backtestName": str(backtest_name)[:100],
        }
        if parameters:
            payload["parameters"] = dict(parameters)
        result = self._post("backtests/create", payload)
        backtest = result.get("backtest") or {}
        backtest_id = backtest.get("backtestId")
        if not backtest_id:
            raise QuantConnectError("QuantConnect did not return a backtest ID")
        return str(backtest_id)

    def read_backtest(self, backtest_id: str) -> dict[str, Any]:
        result = self._post(
            "backtests/read",
            {"projectId": self.config.project_id, "backtestId": str(backtest_id)},
        )
        return result.get("backtest") or result

    def submit_backtest(self, backtest_name: str, parameters=None) -> dict[str, str]:
        """Compile the configured project and submit a backtest; does not wait indefinitely."""
        compile_id = self.create_compile()
        return {
            "compile_id": compile_id,
            "backtest_id": self.create_backtest(compile_id, backtest_name, parameters),
        }


def quantconnect_client_from_env(environ=None, session=None):
    """Return None when cloud backtesting is not configured; never fail app startup."""
    config = QuantConnectConfig.from_env(environ)
    return QuantConnectCloud(config, session=session) if config else None
