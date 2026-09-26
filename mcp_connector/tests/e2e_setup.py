"""Prepare a site for the HTTP end-to-end gate (dev/gates/mcp_e2e_gate.mjs).

Run with:  bench --site <site> execute mcp_connector.tests.e2e_setup.setup
Returns the API credentials of the test users as JSON. Only for development sites.
"""

import frappe

from mcp_connector.domain.action_types import ACTION_TYPE_FIELD, ACTION_TYPES, DOC_OPERATIONS, OPERATION_FIELD
from mcp_connector.infrastructure.policy_source import clear_cache
from mcp_connector.tests import fixtures as F

REQUIRED_ACTIONS = (
	"list_doctypes",
	"describe_doctype",
	"list_documents",
	"get_document",
	"create_document",
	"update_document",
	"count_documents",
	"list_reports",
	"describe_report",
	"run_report",
)


def _profile(name, *, roles=(), users=(), types=(), actions=(), doctypes=(), reports=()):
	if frappe.db.exists("MCP Profile", name):
		frappe.delete_doc("MCP Profile", name, ignore_permissions=True)
	frappe.get_doc(
		{
			"doctype": "MCP Profile",
			"profile_name": name,
			"enabled": 1,
			"roles": [{"role": r} for r in roles],
			"users": [{"user": u} for u in users],
			"actions": [{"action": a} for a in actions],
			"doctypes": [
				{
					"document_type": row["doctype"],
					**{OPERATION_FIELD[op]: int(op in row["ops"]) for op in DOC_OPERATIONS},
					"field_mode": row.get("field_mode", "All Fields"),
					"field_list": row.get("field_list", ""),
					"scope_filters": row.get("scope", ""),
				}
				for row in doctypes
			],
			"reports": [{"report": r["report"], "fixed_filters": r.get("fixed", "")} for r in reports],
			**{ACTION_TYPE_FIELD[t]: int(t in types) for t in ACTION_TYPES},
		}
	).insert(ignore_permissions=True)


def _keys(user):
	from frappe.core.doctype.user.user import generate_keys

	secret = generate_keys(user)["api_secret"]
	return f"{frappe.db.get_value('User', user, 'api_key')}:{secret}"


def setup():
	frappe.only_for("System Manager")
	F.ensure_all()
	disabled = frappe.get_all("MCP Action", filters={"name": ["in", REQUIRED_ACTIONS], "enabled": 0}, pluck="name")
	if disabled:
		frappe.throw(f"Enable these MCP Actions before running the e2e gate: {', '.join(disabled)}")

	read_actions = ["list_doctypes", "describe_doctype", "list_documents", "get_document"]
	_profile(
		"E2E Sales",
		roles=[F.SALES_ROLE],
		types=("Read", "Create", "Update", "Report"),
		# count_documents deliberately left out: per-action toggles must hide it.
		actions=read_actions + ["create_document", "update_document", "list_reports", "describe_report", "run_report"],
		doctypes=[
			{
				"doctype": F.ORDER,
				"ops": ("Read", "Create", "Update"),
				"field_mode": "Exclude Listed Fields",
				"field_list": "secret_note",
				"scope": '{"company": "ACME"}',
			}
		],
		reports=[{"report": F.REPORT, "fixed": '{"company": "ACME"}'}],
	)
	_profile(
		"E2E Viewer",
		roles=[F.VIEWER_ROLE],
		types=("Read", "Create"),
		actions=read_actions + ["create_document"],
		doctypes=[{"doctype": F.ORDER, "ops": ("Read", "Create")}],
	)
	_profile(
		"E2E Capped",
		users=[F.CAPPED_USER],
		types=("Read",),  # Create is off although the action and the operation are selected
		actions=read_actions + ["create_document"],
		doctypes=[{"doctype": F.ORDER, "ops": ("Read", "Create")}],
	)
	if not frappe.db.exists(F.ORDER, {"customer": "E2E Globex"}):
		F.make_order("E2E Acme", "ACME", 150)
		F.make_order("E2E Globex", "Globex", 900)

	settings = frappe.get_single("MCP Settings")
	was_enabled = settings.enabled
	settings.enabled = 1
	settings.save(ignore_permissions=True)
	clear_cache()
	frappe.db.commit()
	return {
		"was_enabled": was_enabled,
		"sales": _keys(F.SALES_USER),
		"viewer": _keys(F.VIEWER_USER),
		"capped": _keys(F.CAPPED_USER),
		"orphan": _keys(F.ORPHAN_USER),
		"globex_order": frappe.db.get_value(F.ORDER, {"customer": "E2E Globex"}, "name"),
		"acme_order": frappe.db.get_value(F.ORDER, {"customer": "E2E Acme"}, "name"),
	}


def set_enabled(enabled):
	frappe.only_for("System Manager")
	settings = frappe.get_single("MCP Settings")
	settings.enabled = int(enabled)
	settings.save(ignore_permissions=True)
	frappe.db.commit()
	return {"enabled": settings.enabled}
