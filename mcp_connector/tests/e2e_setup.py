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


def locked_out_user():
	"""Credentials of a System Manager (not Administrator) for the UI gate's lockout check."""
	frappe.only_for("System Manager")
	F.ensure_system_manager()
	frappe.db.commit()
	return {"user": F.SYSTEM_MANAGER_USER, "password": F.SYSTEM_MANAGER_PASSWORD}


def set_enabled(enabled):
	frappe.only_for("System Manager")
	settings = frappe.get_single("MCP Settings")
	settings.enabled = int(enabled)
	settings.save(ignore_permissions=True)
	frappe.db.commit()
	return {"enabled": settings.enabled}


OAUTH_CLIENT = "MCP E2E OAuth Client"
OAUTH_PASSWORD = "Mcp#Signs-In-2026"  # development fixture only


def oauth_setup(redirect_uri):
	"""An OAuth client redirecting to the gate's local callback, and the sales user able to sign in.

	Also returns the tools the endpoint lists for that user when called in-process, so the gate
	can check the OAuth path sees exactly the same profile."""
	from frappe.utils.password import update_password

	from mcp_connector.api.mcp import build_handler, build_session
	from mcp_connector.infrastructure.settings import load_settings

	keys = setup()
	update_password(F.SALES_USER, OAUTH_PASSWORD)
	name = frappe.db.get_value("OAuth Client", {"app_name": OAUTH_CLIENT}, "name")
	client = frappe.get_doc("OAuth Client", name) if name else frappe.new_doc("OAuth Client")
	client.update(
		{
			"app_name": OAUTH_CLIENT,
			"scopes": "all openid",
			"redirect_uris": redirect_uri,
			"default_redirect_uri": redirect_uri,
			"grant_type": "Authorization Code",
			"response_type": "Code",
			"skip_authorization": 0,
		}
	)
	client.save(ignore_permissions=True)
	frappe.db.set_value("OAuth Bearer Token", {"user": F.SALES_USER, "status": "Active"}, "status", "Revoked")
	frappe.db.commit()

	session = build_session(F.SALES_USER, load_settings())
	listed = build_handler().execute({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, session)
	return {
		"user": F.SALES_USER,
		"password": OAUTH_PASSWORD,
		"client_id": client.client_id,
		"client_secret": client.client_secret,
		"expected_tools": sorted(t["name"] for t in listed["result"]["tools"]),
		"order_doctype": F.ORDER,
		"was_enabled": keys["was_enabled"],
	}


def expire_oauth_tokens(user):
	"""Make the user's access tokens expire now, so the client has to use its refresh token."""
	frappe.only_for("System Manager")
	for name in frappe.get_all("OAuth Bearer Token", filters={"user": user, "status": "Active"}, pluck="name"):
		frappe.db.set_value("OAuth Bearer Token", name, "expiration_time", frappe.utils.add_to_date(None, minutes=-5))
	frappe.db.commit()
	return {"expired": True}


def set_user_enabled(user, enabled):
	frappe.only_for("System Manager")
	frappe.db.set_value("User", user, "enabled", int(enabled))
	frappe.db.commit()
	return {"enabled": int(enabled)}
