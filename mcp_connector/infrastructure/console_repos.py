"""Frappe implementations of the console repositories. Every write goes through
doc.save()/insert() so DocType validation and Frappe permissions apply."""

import json
from typing import Optional

import frappe
from frappe.utils import add_to_date, get_fullname, now_datetime

from mcp_connector.application.console_dto import ActionPayload, LogQuery, ProfilePayload, SettingsPayload
from mcp_connector.application.dto import ServerSettings
from mcp_connector.domain.action_types import ACTION_TYPE_FIELD, ACTION_TYPES, DOC_OPERATIONS, OPERATION_FIELD
from mcp_connector.domain.catalog import BUILTIN_BY_NAME
from mcp_connector.domain.errors import InvalidArguments, NotFound
from mcp_connector.domain.policy import parse_field_list
from mcp_connector.domain.protected import is_exposable, is_writable
from mcp_connector.infrastructure.policy_source import (
	action_record,
	load_actions,
	profile_record,
	spec_from_action,
	spec_from_record,
)
from mcp_connector.infrastructure.settings import SETTINGS_DOCTYPE, load_settings

PROFILE = "MCP Profile"
ACTION = "MCP Action"
AUDIT = "MCP Audit Log"


def _json_or_none(raw):
	if not raw:
		return None
	try:
		return json.loads(raw)
	except ValueError:
		return None


def _ts(value) -> str:
	return str(value)[:19] if value else ""


class FrappeProfileRepository:
	def summaries(self) -> list:
		rows = []
		for name in frappe.get_all(PROFILE, pluck="name", order_by="profile_name asc"):
			doc = frappe.get_doc(PROFILE, name)
			rows.append(
				{
					"name": doc.name,
					"enabled": bool(doc.enabled),
					"description": doc.description or "",
					"roles": [r.role for r in doc.roles],
					"users": [u.user for u in doc.users],
					"action_types": [t for t in ACTION_TYPES if doc.get(ACTION_TYPE_FIELD[t])],
					"actions": len(doc.actions),
					"doctypes": len(doc.doctypes),
					"reports": len(doc.reports),
					"modified": _ts(doc.modified),
				}
			)
		return rows

	def get(self, name: str) -> Optional[dict]:
		if not name or not frappe.db.exists(PROFILE, name):
			return None
		doc = frappe.get_doc(PROFILE, name)
		record = profile_record(doc)
		doctypes = []
		for row in record["doctypes"]:
			exists = bool(frappe.db.exists("DocType", row["doctype"]))
			meta = frappe.get_meta(row["doctype"]) if exists else None
			doctypes.append(
				{
					"doctype": row["doctype"],
					"operations": row["operations"],
					"field_mode": row["field_mode"],
					"field_list": sorted(parse_field_list(row["field_list"])),
					"scope": _json_or_none(row["scope_filters"]),
					"notes": row["notes"],
					"meta": doctype_flags(row["doctype"], meta),
				}
			)
		reports = []
		for row in record["reports"]:
			reports.append(
				{
					"report": row["report"],
					"fixed_filters": _json_or_none(row["fixed_filters"]),
					"notes": row["notes"],
					"meta": report_flags(row["report"]),
				}
			)
		return {
			"name": doc.name,
			"profile_name": doc.profile_name,
			"enabled": bool(doc.enabled),
			"description": doc.description or "",
			"roles": record["roles"],
			"users": record["users"],
			"action_types": record["action_types"],
			"actions": record["actions"],
			"doctypes": doctypes,
			"reports": reports,
			"modified": _ts(doc.modified),
		}

	def save(self, payload: ProfilePayload) -> str:
		if payload.original_name:
			if not frappe.db.exists(PROFILE, payload.original_name):
				raise NotFound(f"MCP Profile {payload.original_name} does not exist")
			doc = frappe.get_doc(PROFILE, payload.original_name)
		else:
			if frappe.db.exists(PROFILE, payload.profile_name):
				raise InvalidArguments(f"A profile named {payload.profile_name} already exists")
			doc = frappe.new_doc(PROFILE)
			doc.profile_name = payload.profile_name

		doc.enabled = int(payload.enabled)
		doc.description = payload.description
		doc.set("roles", [{"role": r} for r in payload.roles])
		doc.set("users", [{"user": u} for u in payload.users])
		for action_type in ACTION_TYPES:
			doc.set(ACTION_TYPE_FIELD[action_type], int(action_type in payload.action_types))
		doc.set("actions", [{"action": a} for a in payload.actions])
		doc.set(
			"doctypes",
			[
				{
					"document_type": row.doctype,
					**{OPERATION_FIELD[op]: int(op in row.operations) for op in DOC_OPERATIONS},
					"field_mode": row.field_mode,
					"field_list": "\n".join(row.field_list),
					"scope_filters": json.dumps(row.scope) if row.scope else "",
					"notes": row.notes,
				}
				for row in payload.doctypes
			],
		)
		doc.set(
			"reports",
			[
				{
					"report": row.report,
					"fixed_filters": json.dumps(row.fixed_filters) if row.fixed_filters else "",
					"notes": row.notes,
				}
				for row in payload.reports
			],
		)
		doc.save()
		name = doc.name
		if payload.original_name and payload.profile_name != doc.name:
			name = frappe.rename_doc(PROFILE, doc.name, payload.profile_name)
			frappe.db.set_value(PROFILE, name, "profile_name", payload.profile_name)
		return name

	def delete(self, name: str) -> None:
		frappe.delete_doc(PROFILE, name)

	def duplicate(self, name: str, new_name: str) -> str:
		if frappe.db.exists(PROFILE, new_name):
			raise InvalidArguments(f"A profile named {new_name} already exists")
		copy = frappe.copy_doc(frappe.get_doc(PROFILE, name))
		copy.profile_name = new_name
		copy.enabled = 0
		copy.insert()
		return copy.name

	def specs(self) -> list:
		return [spec_from_record(profile_record(frappe.get_doc(PROFILE, n))) for n in frappe.get_all(PROFILE, pluck="name")]

	def action_specs(self) -> list:
		return load_actions()


def doctype_flags(doctype: str, meta) -> dict:
	if meta is None:
		return {"exists": False}
	return {
		"exists": True,
		"module": meta.module,
		"is_submittable": bool(meta.is_submittable),
		"issingle": bool(meta.issingle),
		"istable": bool(meta.istable),
		"exposable": is_exposable(doctype),
		"writable": is_writable(doctype),
	}


def report_flags(report: str) -> dict:
	if not frappe.db.exists("Report", report):
		return {"exists": False}
	values = frappe.db.get_value("Report", report, ["report_type", "ref_doctype", "disabled"], as_dict=True)
	return {"exists": True, "report_type": values.report_type, "ref_doctype": values.ref_doctype, "disabled": bool(values.disabled)}


class FrappeActionRepository:
	def list(self) -> list:
		used = {}
		for row in frappe.get_all("MCP Profile Action", fields=["action", "parent"], limit_page_length=0):
			used.setdefault(row.action, set()).add(row.parent)
		rows = []
		for row in frappe.get_all(
			ACTION,
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
			limit_page_length=0,
		):
			record = action_record(row)
			builtin = BUILTIN_BY_NAME.get(row.name) if row.is_standard else None
			record["default_description"] = builtin.description if builtin else ""
			record["input_schema"] = _json_or_none(record["input_schema"])
			record["used_by"] = sorted(used.get(row.name, ()))
			rows.append(record)
		order = {t: i for i, t in enumerate(ACTION_TYPES)}
		builtin_order = {name: i for i, name in enumerate(BUILTIN_BY_NAME)}
		rows.sort(key=lambda r: (order.get(r["action_type"], 99), builtin_order.get(r["name"], 999), r["name"]))
		return rows

	def save(self, payload: ActionPayload) -> str:
		if payload.is_new:
			if frappe.db.exists(ACTION, payload.tool_name):
				raise InvalidArguments(f"An action named {payload.tool_name} already exists")
			doc = frappe.new_doc(ACTION)
			doc.tool_name = payload.tool_name
		else:
			if not frappe.db.exists(ACTION, payload.tool_name):
				raise NotFound(f"MCP Action {payload.tool_name} does not exist")
			doc = frappe.get_doc(ACTION, payload.tool_name)

		doc.enabled = int(payload.enabled)
		doc.description = payload.description
		if not doc.is_standard:
			doc.title = payload.title
			doc.action_type = payload.action_type
			doc.method = payload.method
			doc.input_schema = json.dumps(payload.input_schema) if payload.input_schema else ""
			doc.read_only_hint = int(payload.read_only_hint)
			doc.destructive_hint = int(payload.destructive_hint)
		doc.save()
		return doc.name

	def delete(self, name: str) -> None:
		if not frappe.db.exists(ACTION, name):
			raise NotFound(f"MCP Action {name} does not exist")
		frappe.delete_doc(ACTION, name)


SETTINGS_FIELDS = (
	"enabled",
	"server_name",
	"instructions",
	"default_page_size",
	"max_page_size",
	"max_report_rows",
	"rate_limit_per_minute",
	"audit_mode",
	"log_arguments",
	"log_retention_days",
)


class FrappeSettingsRepository:
	def get(self) -> dict:
		settings = load_settings()
		data = {field: getattr(settings, field) for field in SETTINGS_FIELDS}
		data["allowed_origins"] = list(settings.allowed_origins)
		return data

	def server_settings(self) -> ServerSettings:
		return load_settings()

	def save(self, payload: SettingsPayload) -> None:
		doc = frappe.get_single(SETTINGS_DOCTYPE)
		for field in SETTINGS_FIELDS:
			value = getattr(payload, field)
			doc.set(field, int(value) if isinstance(value, bool) else value)
		doc.allowed_origins = "\n".join(payload.allowed_origins)
		doc.save()


LOG_LIST_FIELDS = [
	"name",
	"creation",
	"user",
	"tool",
	"status",
	"action_type",
	"rpc_method",
	"document_type",
	"document_name",
	"report",
	"duration_ms",
	"result_summary",
	"error",
]


class FrappeAuditQuery:
	def entries_since(self, hours: int, limit: int) -> tuple:
		now = now_datetime()
		rows = frappe.get_all(
			AUDIT,
			filters={"creation": [">=", add_to_date(now, hours=-hours)]},
			fields=["name", "creation", "status", "tool", "user", "duration_ms", "document_type", "document_name", "report"],
			order_by="creation desc",
			limit_page_length=limit,
		)
		for row in rows:
			row["creation"] = _ts(row["creation"])
		return _ts(now), [dict(r) for r in rows]

	def search(self, query: LogQuery) -> list:
		filters = {}
		if query.status:
			filters["status"] = query.status
		if query.user:
			filters["user"] = query.user
		if query.tool:
			filters["tool"] = query.tool
		if query.doctype:
			filters["document_type"] = query.doctype
		if query.hours:
			filters["creation"] = [">=", add_to_date(now_datetime(), hours=-query.hours)]
		rows = frappe.get_all(
			AUDIT,
			filters=filters,
			fields=LOG_LIST_FIELDS,
			order_by="creation desc",
			limit_start=query.start,
			limit_page_length=query.limit + 1,
		)
		result = []
		for row in rows:
			row = dict(row)
			row["creation"] = _ts(row["creation"])
			error = row.get("error") or ""
			row["error"] = error.strip().splitlines()[-1][:300] if error.strip() else ""
			result.append(row)
		return result

	def get(self, name: str) -> Optional[dict]:
		if not frappe.db.exists(AUDIT, name):
			return None
		doc = frappe.get_doc(AUDIT, name)
		data = {field: doc.get(field) for field in LOG_LIST_FIELDS}
		data.update(
			{
				"creation": _ts(doc.creation),
				"arguments": doc.arguments,
				"profiles": doc.profiles,
				"client_ip": doc.client_ip,
				"user_agent": doc.user_agent,
			}
		)
		return data


class FrappeCatalogRepository:
	def doctypes(self) -> list:
		rows = frappe.get_all(
			"DocType",
			filters={"istable": 0, "is_virtual": 0},
			fields=["name", "module", "issingle", "is_submittable", "custom"],
			order_by="module asc, name asc",
			limit_page_length=0,
		)
		return [
			{
				"name": r.name,
				"module": r.module or "",
				"issingle": bool(r.issingle),
				"is_submittable": bool(r.is_submittable),
				"custom": bool(r.custom),
			}
			for r in rows
		]

	def reports(self) -> list:
		rows = frappe.get_all(
			"Report",
			filters={"disabled": 0},
			fields=["name", "module", "report_type", "ref_doctype"],
			order_by="module asc, name asc",
			limit_page_length=0,
		)
		return [
			{"name": r.name, "module": r.module or "", "report_type": r.report_type, "ref_doctype": r.ref_doctype or ""}
			for r in rows
		]


class FrappeDirectory:
	def exists(self, user: str) -> bool:
		return bool(frappe.db.exists("User", user))

	def roles_of(self, user: str) -> list:
		return frappe.get_roles(user)

	def full_name(self, user: str) -> str:
		return get_fullname(user)

	def is_enabled(self, user: str) -> bool:
		return bool(frappe.db.get_value("User", user, "enabled"))

	def count_enabled_users(self, roles: tuple, users: tuple) -> int:
		names = set(users)
		if roles:
			names.update(
				frappe.get_all("Has Role", filters={"role": ["in", list(roles)], "parenttype": "User"}, pluck="parent")
			)
		names.discard("Guest")
		if not names:
			return 0
		return len(frappe.get_all("User", filters={"name": ["in", list(names)], "enabled": 1}, pluck="name"))

	def has_permission(self, doctype: str, ptype: str, user: str) -> bool:
		if not frappe.db.exists("DocType", doctype):
			return False
		return bool(frappe.has_permission(doctype, ptype, user=user))

	def report_permitted(self, report: str, user: str) -> bool:
		if not frappe.db.exists("Report", report):
			return False
		doc = frappe.get_cached_doc("Report", report)
		if doc.disabled:
			return False
		allowed = [r.role for r in doc.roles]
		custom = frappe.db.get_value("Custom Role", {"report": report}, "name")
		if custom:
			allowed = [r.role for r in frappe.get_doc("Custom Role", custom).roles]
		if allowed and not set(allowed).intersection(frappe.get_roles(user)):
			return False
		return not doc.ref_doctype or bool(frappe.has_permission(doc.ref_doctype, "report", user=user))

	def generate_keys(self, user: str) -> dict:
		from frappe.core.doctype.user.user import generate_keys

		secret = generate_keys(user)["api_secret"]
		return {"user": user, "api_key": frappe.db.get_value("User", user, "api_key"), "api_secret": secret}
