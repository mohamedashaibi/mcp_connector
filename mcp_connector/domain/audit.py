"""What gets written to the audit trail, and how secrets are kept out of it."""

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .action_types import REPORT, READ

SUCCESS = "Success"
DENIED = "Denied"
ERROR = "Error"

AUDIT_ALL = "All Requests"
AUDIT_WRITES_AND_FAILURES = "Writes and Failures"
AUDIT_DISABLED = "Disabled"
AUDIT_MODES = (AUDIT_ALL, AUDIT_WRITES_AND_FAILURES, AUDIT_DISABLED)

_SECRET_TOKENS = frozenset(
	{"password", "passwd", "pwd", "secret", "token", "apikey", "otp", "pin", "iban", "cvv", "authorization"}
)
REDACTED = "••••••"


def is_secret_key(key: str) -> bool:
	lowered = key.lower()
	if "password" in lowered or "api_key" in lowered or "apikey" in lowered:
		return True
	return any(token in _SECRET_TOKENS for token in re.split(r"[_\W]+", lowered))


@dataclass
class AuditEntry:
	user: str
	rpc_method: str
	status: str
	tool: str = ""
	action_type: str = ""
	document_type: str = ""
	document_name: str = ""
	report: str = ""
	duration_ms: int = 0
	arguments: Any = None
	result_summary: str = ""
	error: str = ""
	profiles: tuple = ()
	client_ip: str = ""
	user_agent: str = ""
	extra: dict = field(default_factory=dict)


def should_record(mode: str, action_type: str, status: str) -> bool:
	if mode == AUDIT_DISABLED:
		return False
	if mode == AUDIT_WRITES_AND_FAILURES:
		return status != SUCCESS or action_type not in (READ, REPORT)
	return True


def redact(value: Any, depth: int = 0) -> Any:
	if depth > 6:
		return "…"
	if isinstance(value, dict):
		return {
			k: (REDACTED if isinstance(k, str) and is_secret_key(k) else redact(v, depth + 1))
			for k, v in value.items()
		}
	if isinstance(value, list):
		return [redact(v, depth + 1) for v in value[:50]] + (["…"] if len(value) > 50 else [])
	return value


def serialize_arguments(arguments: Any, max_chars: int = 4000) -> str:
	text = json.dumps(redact(arguments), default=str, ensure_ascii=False, indent=1, sort_keys=True)
	return text if len(text) <= max_chars else text[: max_chars - 1] + "…"
