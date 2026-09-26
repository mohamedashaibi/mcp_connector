"""Loads MCP Profiles and MCP Actions into domain objects, cached per site in Redis."""

import json

import frappe

from mcp_connector.domain.action_types import ACTION_TYPE_FIELD, ACTION_TYPES, DOC_OPERATIONS, OPERATION_FIELD
from mcp_connector.domain.catalog import BUILTIN_BY_NAME
from mcp_connector.domain.errors import InvalidArguments
from mcp_connector.domain.filters import normalize_filters
from mcp_connector.domain.policy import (
	ALL_FIELDS,
	FIELD_MODES,
	ActionSpec,
	DoctypeRule,
	FieldRule,
	ProfileSpec,
	ReportRule,
	parse_field_list,
)

CACHE_KEY = "mcp_connector:policy:v2"
CACHE_SECONDS = 3600


def clear_cache(*args, **kwargs) -> None:
	frappe.cache().delete_keys(CACHE_KEY)


def _version() -> str:
	"""Changes whenever a profile or action is saved, added or removed -- read in the same
	transaction as the data, so a snapshot can never be cached under a newer version."""
	from frappe.query_builder.functions import Count, Max

	parts = []
	for doctype in ("MCP Profile", "MCP Action"):
		table = frappe.qb.DocType(doctype)
		count, latest = frappe.qb.from_(table).select(Count("*"), Max(table.modified)).run()[0]
		parts.append(f"{count}:{latest}")
	return "|".join(parts)


def _loads(raw, default):
	if raw in (None, ""):
		return default
	return json.loads(raw) if isinstance(raw, str) else raw


def profile_record(doc) -> dict:
	"""A JSON-safe snapshot of an MCP Profile document."""
	return {
		"name": doc.name,
		"enabled": bool(doc.enabled),
		"description": doc.description or "",
		"roles": [r.role for r in doc.roles],
		"users": [u.user for u in doc.users],
		"action_types": [t for t in ACTION_TYPES if doc.get(ACTION_TYPE_FIELD[t])],
		"actions": [a.action for a in doc.actions],
		"doctypes": [
			{
				"doctype": row.document_type,
				"operations": [op for op in DOC_OPERATIONS if row.get(OPERATION_FIELD[op])],
				"field_mode": row.field_mode or ALL_FIELDS,
				"field_list": row.field_list or "",
				"scope_filters": row.scope_filters or "",
				"notes": row.notes or "",
			}
			for row in doc.doctypes
		],
		"reports": [
			{"report": row.report, "fixed_filters": row.fixed_filters or "", "notes": row.notes or ""}
			for row in doc.reports
		],
	}


def parse_scope(raw) -> tuple:
	return normalize_filters(_loads(raw, None))


def parse_fixed_filters(raw) -> dict:
	value = _loads(raw, {})
	if not isinstance(value, dict):
		raise InvalidArguments("Fixed filters must be a JSON object")
	return value


def spec_from_record(record: dict) -> ProfileSpec:
	rules = []
	for row in record["doctypes"]:
		try:
			scope = parse_scope(row["scope_filters"])
		except (ValueError, InvalidArguments):
			# Fail closed: an unreadable scope must never widen access.
			frappe.log_error(title=f"MCP Profile {record['name']}: invalid scope for {row['doctype']}")
			continue
		mode = row["field_mode"] if row["field_mode"] in FIELD_MODES else ALL_FIELDS
		rules.append(
			DoctypeRule(
				doctype=row["doctype"],
				operations=frozenset(row["operations"]),
				field_rule=FieldRule(mode, parse_field_list(row["field_list"])) if mode != ALL_FIELDS else FieldRule(),
				scope=scope,
				notes=row["notes"],
			)
		)
	reports = []
	for row in record["reports"]:
		try:
			fixed = parse_fixed_filters(row["fixed_filters"])
		except (ValueError, InvalidArguments):
			frappe.log_error(title=f"MCP Profile {record['name']}: invalid fixed filters for {row['report']}")
			continue
		reports.append(ReportRule(report=row["report"], fixed_filters=fixed, notes=row["notes"]))
	return ProfileSpec(
		name=record["name"],
		enabled=record["enabled"],
		description=record["description"],
		roles=frozenset(record["roles"]),
		users=frozenset(record["users"]),
		action_types=frozenset(record["action_types"]),
		actions=frozenset(record["actions"]),
		doctypes=tuple(rules),
		reports=tuple(reports),
	)


def action_record(row) -> dict:
	return {
		"name": row.name,
		"action_type": row.action_type,
		"enabled": bool(row.enabled),
		"is_standard": bool(row.is_standard),
		"title": row.title or "",
		"description": row.description or "",
		"method": row.method or "",
		"input_schema": row.input_schema or "",
		"read_only_hint": bool(row.read_only_hint),
		"destructive_hint": bool(row.destructive_hint),
	}


def spec_from_action(record: dict) -> ActionSpec:
	schema = None
	if not record["is_standard"]:
		try:
			schema = _loads(record["input_schema"], None)
		except ValueError:
			schema = None
	builtin = BUILTIN_BY_NAME.get(record["name"]) if record["is_standard"] else None
	return ActionSpec(
		name=record["name"],
		# A standard action's type always comes from code, whatever the row says.
		action_type=builtin.action_type if builtin else record["action_type"],
		enabled=record["enabled"],
		is_standard=record["is_standard"],
		title=record["title"],
		description=record["description"],
		method=record["method"],
		input_schema=schema,
		read_only_hint=builtin.read_only if builtin else record["read_only_hint"],
		destructive_hint=builtin.destructive if builtin else record["destructive_hint"],
	)


def _snapshot() -> dict:
	key = f"{CACHE_KEY}:{_version()}"
	data = frappe.cache().get_value(key)
	if data is None:
		profiles = [profile_record(frappe.get_doc("MCP Profile", n)) for n in frappe.get_all("MCP Profile", pluck="name")]
		actions = [
			action_record(row)
			for row in frappe.get_all(
				"MCP Action",
				fields=[
					"name",
					"action_type",
					"enabled",
					"is_standard",
					"title",
					"description",
					"method",
					"input_schema",
					"read_only_hint",
					"destructive_hint",
				],
			)
		]
		data = {"profiles": profiles, "actions": actions}
		frappe.cache().set_value(key, data, expires_in_sec=CACHE_SECONDS)
	return data


def load_profiles() -> list:
	return [spec_from_record(r) for r in _snapshot()["profiles"]]


def load_actions() -> list:
	return [spec_from_action(r) for r in _snapshot()["actions"]]
