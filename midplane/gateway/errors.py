"""Standard error body for every non-2xx response."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class APIError(Exception):
    status_code: int
    code: str
    message: str
    details: list[dict[str, str]] | None = None

    def body(self, request_id: str) -> dict:
        error: dict = {
            "code": self.code,
            "message": self.message,
            "request_id": request_id,
        }
        if self.details:
            error["details"] = self.details
        return {"error": error}
