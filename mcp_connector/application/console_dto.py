"""Strictly parsed payloads accepted by the admin console.

Every parser rejects unknown keys and wrong types before anything touches the
database; semantic checks (does the DocType exist, is it submittable…) run in
the DocType controllers so desk forms and the console share them.
"""

from dataclasses import dataclass
from typing import Any

from mcp_connector.domain.action_types import ACTION_TYPES, DOC_OPERATIONS
from mcp_connector.domain.audit import AUDIT_MODES
from mcp_connector.domain.errors import InvalidArguments
from mcp_connector.domain.policy import FIELD_MODES


def _obj(value: Any, what: str, allowed: set, required: set = frozenset()) -> dict:
	if not isinstance(value, dict):
		raise InvalidArguments(f"{what} must be an object")
	unknown = sorted(set(value) - allowed)
	if unknown:
		raise InvalidArguments(f"Unexpected field(s) in {what}: {', '.join(unknown)}")
	missing = sorted(k for k in required if k not in value)
	if missing:
		raise InvalidArguments(f"Missing {', '.join(missing)} in {what}")
	return value


def _str(value: Any, what: str, max_len: int = 140, required: bool = False) -> str:
	if value is None:
		value = ""
	if not isinstance(value, str):
		raise InvalidArguments(f"{what} must be text")
	value = value.strip()
	if required and not value:
		raise InvalidArguments(f"{what} is required")
	if len(value) > max_len:
		raise InvalidArguments(f"{what} is longer than {max_len} characters")
	return value


def _bool(value: Any, what: str) -> bool:
	if isinstance(value, bool):
		return value
	if value in (0, 1):
		return bool(value)
	raise InvalidArguments(f"{what} must be true or false")


def _int(value: Any, what: str, low: int, high: int) -> int:
	if isinstance(value, bool) or not isinstance(value, int):
		raise InvalidArguments(f"{what} must be a whole number")
	if not low <= value <= high:
		raise InvalidArguments(f"{what} must be between {low} and {high}")
	return value


def _str_list(value: Any, what: str, max_items: int = 500, choices=None) -> tuple:
	if value is None:
		return ()
	if not isinstance(value, list) or len(value) > max_items:
		raise InvalidArguments(f"{what} must be a list of at most {max_items} items")
	items = []
	for item in value:
		item = _str(item, what, required=True)
		if choices is not None and item not in choices:
			raise InvalidArguments(f"{item!r} is not a valid {what}")
		if item not in items:
			items.append(item)
	return tuple(items)


def _json_value(value: Any, what: str, kinds: tuple) -> Any:
	if value in (None, "", [], {}):
		return None
	if not isinstance(value, kinds):
		raise InvalidArguments(f"{what} has the wrong shape")
	return value


@dataclass(frozen=True)
class DoctypeRowPayload:
	doctype: str
	operations: tuple
	field_mode: str
	field_list: tuple
	scope: Any  # dict | list | None
	notes: str


@dataclass(frozen=True)
class ReportRowPayload:
	report: str
	fixed_filters: Any  # dict | None
	notes: str


@dataclass(frozen=True)
class ProfilePayload:
	original_name: str  # "" for a new profile
	profile_name: str
	enabled: bool
	description: str
	roles: tuple
	users: tuple
	action_types: tuple
	actions: tuple
	doctypes: tuple
	reports: tuple


PROFILE_KEYS = {
	"original_name",
	"profile_name",
	"enabled",
	"description",
	"roles",
	"users",
	"action_types",
	"actions",
	"doctypes",
	"reports",
}


def parse_profile(raw: Any) -> ProfilePayload:
	data = _obj(raw, "profile", PROFILE_KEYS, {"profile_name"})
	doctype_rows = data.get("doctypes") or []
	report_rows = data.get("reports") or []
	if not isinstance(doctype_rows, list) or len(doctype_rows) > 500:
		raise InvalidArguments("doctypes must be a list of at most 500 rows")
	if not isinstance(report_rows, list) or len(report_rows) > 300:
		raise InvalidArguments("reports must be a list of at most 300 rows")

	doctypes = []
	for row in doctype_rows:
		row = _obj(
			row, "DocType row", {"doctype", "operations", "field_mode", "field_list", "scope", "notes"}, {"doctype"}
		)
		field_mode = _str(row.get("field_mode") or FIELD_MODES[0], "field_mode")
		if field_mode not in FIELD_MODES:
			raise InvalidArguments(f"field_mode must be one of {', '.join(FIELD_MODES)}")
		doctypes.append(
			DoctypeRowPayload(
				doctype=_str(row["doctype"], "doctype", required=True),
				operations=_str_list(row.get("operations"), "operation", choices=DOC_OPERATIONS),
				field_mode=field_mode,
				field_list=_str_list(row.get("field_list"), "field", max_items=1000),
				scope=_json_value(row.get("scope"), "scope", (dict, list)),
				notes=_str(row.get("notes"), "notes", max_len=1000),
			)
		)

	reports = []
	for row in report_rows:
		row = _obj(row, "report row", {"report", "fixed_filters", "notes"}, {"report"})
		reports.append(
			ReportRowPayload(
				report=_str(row["report"], "report", required=True),
				fixed_filters=_json_value(row.get("fixed_filters"), "fixed_filters", (dict,)),
				notes=_str(row.get("notes"), "notes", max_len=1000),
			)
		)

	return ProfilePayload(
		original_name=_str(data.get("original_name"), "original_name"),
		profile_name=_str(data["profile_name"], "Profile name", required=True),
		enabled=_bool(data.get("enabled", True), "enabled"),
		description=_str(data.get("description"), "description", max_len=2000),
		roles=_str_list(data.get("roles"), "role"),
		users=_str_list(data.get("users"), "user"),
		action_types=_str_list(data.get("action_types"), "action type", choices=ACTION_TYPES),
		actions=_str_list(data.get("actions"), "action"),
		doctypes=tuple(doctypes),
		reports=tuple(reports),
	)


@dataclass(frozen=True)
class ActionPayload:
	is_new: bool
	tool_name: str
	title: str
	action_type: str
	enabled: bool
	description: str
	method: str
	input_schema: Any
	read_only_hint: bool
	destructive_hint: bool


ACTION_KEYS = {
	"is_new",
	"tool_name",
	"title",
	"action_type",
	"enabled",
	"description",
	"method",
	"input_schema",
	"read_only_hint",
	"destructive_hint",
}


def parse_action(raw: Any) -> ActionPayload:
	data = _obj(raw, "action", ACTION_KEYS, {"tool_name"})
	action_type = _str(data.get("action_type") or "Custom", "action_type")
	if action_type not in ACTION_TYPES:
		raise InvalidArguments(f"action_type must be one of {', '.join(ACTION_TYPES)}")
	return ActionPayload(
		is_new=_bool(data.get("is_new", False), "is_new"),
		tool_name=_str(data["tool_name"], "Tool name", max_len=64, required=True),
		title=_str(data.get("title"), "title"),
		action_type=action_type,
		enabled=_bool(data.get("enabled", True), "enabled"),
		description=_str(data.get("description"), "description", max_len=4000),
		method=_str(data.get("method"), "method", max_len=240),
		input_schema=_json_value(data.get("input_schema"), "input_schema", (dict,)),
		read_only_hint=_bool(data.get("read_only_hint", False), "read_only_hint"),
		destructive_hint=_bool(data.get("destructive_hint", False), "destructive_hint"),
	)


@dataclass(frozen=True)
class SettingsPayload:
	enabled: bool
	server_name: str
	instructions: str
	default_page_size: int
	max_page_size: int
	max_report_rows: int
	rate_limit_per_minute: int
	audit_mode: str
	log_arguments: bool
	log_retention_days: int
	allowed_origins: tuple


def parse_settings(raw: Any) -> SettingsPayload:
	keys = set(SettingsPayload.__dataclass_fields__)
	data = _obj(raw, "settings", keys, keys)
	audit_mode = _str(data["audit_mode"], "audit_mode")
	if audit_mode not in AUDIT_MODES:
		raise InvalidArguments(f"audit_mode must be one of {', '.join(AUDIT_MODES)}")
	return SettingsPayload(
		enabled=_bool(data["enabled"], "enabled"),
		server_name=_str(data["server_name"], "Server name", required=True),
		instructions=_str(data["instructions"], "instructions", max_len=8000),
		default_page_size=_int(data["default_page_size"], "Default page size", 1, 500),
		max_page_size=_int(data["max_page_size"], "Maximum page size", 1, 500),
		max_report_rows=_int(data["max_report_rows"], "Maximum report rows", 1, 10000),
		rate_limit_per_minute=_int(data["rate_limit_per_minute"], "Requests per minute", 0, 100000),
		audit_mode=audit_mode,
		log_arguments=_bool(data["log_arguments"], "log_arguments"),
		log_retention_days=_int(data["log_retention_days"], "Log retention", 0, 3650),
		allowed_origins=_str_list(data["allowed_origins"], "origin", max_items=50),
	)


@dataclass(frozen=True)
class LogQuery:
	status: str
	user: str
	tool: str
	doctype: str
	hours: int
	start: int
	limit: int


def parse_log_query(raw: Any) -> LogQuery:
	data = _obj(raw or {}, "log query", {"status", "user", "tool", "doctype", "hours", "start", "limit"})
	status = _str(data.get("status"), "status")
	if status not in ("", "Success", "Denied", "Error"):
		raise InvalidArguments("status must be Success, Denied or Error")
	return LogQuery(
		status=status,
		user=_str(data.get("user"), "user"),
		tool=_str(data.get("tool"), "tool"),
		doctype=_str(data.get("doctype"), "doctype"),
		hours=_int(data.get("hours", 0), "hours", 0, 24 * 366),
		start=_int(data.get("start", 0), "start", 0, 10_000_000),
		limit=_int(data.get("limit", 50), "limit", 1, 200),
	)
