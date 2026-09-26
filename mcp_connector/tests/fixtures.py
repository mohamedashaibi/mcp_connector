"""Idempotent test fixtures shared by the Frappe integration suite and the HTTP e2e gate.

Creates custom DocTypes (a submittable order with a child table and a
permlevel-1 field, and a workflow-driven request), roles, users, a query
report and an API server script. DocType creation is DDL and commits, so
fixtures are created once and reused rather than rolled back.
"""

import frappe

SALES_ROLE = "MCP Test Sales"
VIEWER_ROLE = "MCP Test Viewer"
ORPHAN_ROLE = "MCP Test Orphan"
CAPPED_ROLE = "MCP Test Capped"

SALES_USER = "mcp-sales@example.com"
VIEWER_USER = "mcp-viewer@example.com"
ORPHAN_USER = "mcp-orphan@example.com"
CAPPED_USER = "mcp-capped@example.com"
SYSTEM_MANAGER_USER = "mcp-sysmanager@example.com"
SYSTEM_MANAGER_PASSWORD = "Mcp#Locked-Out-2026"  # development fixture only

ORDER = "MCP Test Order"
ORDER_ITEM = "MCP Test Order Item"
REQUEST = "MCP Test Request"
REPORT = "MCP Test Orders By Company"
BUILDER_REPORT = "MCP Test Order Builder"
WORKFLOW = "MCP Test Request Approval"
SERVER_SCRIPT = "MCP Test Echo"
SERVER_SCRIPT_METHOD = "mcp_test_echo"


def _ensure(doctype: str, name: str, values: dict):
	if not frappe.db.exists(doctype, name):
		frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True)


def ensure_roles():
	for role in (SALES_ROLE, VIEWER_ROLE, ORPHAN_ROLE, CAPPED_ROLE):
		_ensure("Role", role, {"role_name": role, "desk_access": 1})


def ensure_users():
	for email, first_name, role in (
		(SALES_USER, "Sally Sales", SALES_ROLE),
		(VIEWER_USER, "Victor Viewer", VIEWER_ROLE),
		(ORPHAN_USER, "Olga Orphan", ORPHAN_ROLE),
		(CAPPED_USER, "Casey Capped", CAPPED_ROLE),
	):
		if not frappe.db.exists("User", email):
			user = frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": first_name, "send_welcome_email": 0, "user_type": "System User"}
			)
			user.append("roles", {"role": role})
			user.insert(ignore_permissions=True)


def ensure_system_manager():
	"""A System Manager who is not the Administrator: must never see or manage the connector."""
	if not frappe.db.exists("User", SYSTEM_MANAGER_USER):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": SYSTEM_MANAGER_USER,
				"first_name": "Sam Sysmanager",
				"send_welcome_email": 0,
				"new_password": SYSTEM_MANAGER_PASSWORD,
			}
		)
		user.append("roles", {"role": "System Manager"})
		user.insert(ignore_permissions=True)


def _perm(role, **flags):
	# DocPerm defaults write/create/delete to 1, so every flag is spelled out.
	base = {"read": 1, "report": 1, "write": 0, "create": 0, "delete": 0, "submit": 0, "cancel": 0, "amend": 0}
	return {"role": role, **base, **flags}


def ensure_doctypes():
	_ensure(
		"DocType",
		ORDER_ITEM,
		{
			"name": ORDER_ITEM,
			"module": "MCP Connector",
			"custom": 1,
			"istable": 1,
			"fields": [
				{"fieldname": "item", "fieldtype": "Data", "label": "Item", "in_list_view": 1},
				{"fieldname": "qty", "fieldtype": "Int", "label": "Qty", "in_list_view": 1},
				{"fieldname": "rate", "fieldtype": "Currency", "label": "Rate", "read_only": 1},
			],
		},
	)
	_ensure(
		"DocType",
		ORDER,
		{
			"name": ORDER,
			"module": "MCP Connector",
			"custom": 1,
			"is_submittable": 1,
			"autoname": "hash",
			"title_field": "customer",
			"search_fields": "company",
			"fields": [
				{"fieldname": "customer", "fieldtype": "Data", "label": "Customer", "in_list_view": 1, "reqd": 1},
				{"fieldname": "company", "fieldtype": "Data", "label": "Company", "in_list_view": 1},
				{"fieldname": "amount", "fieldtype": "Currency", "label": "Amount"},
				{"fieldname": "secret_note", "fieldtype": "Data", "label": "Secret Note"},
				{"fieldname": "internal_code", "fieldtype": "Data", "label": "Internal Code", "permlevel": 1},
				{"fieldname": "items", "fieldtype": "Table", "label": "Items", "options": ORDER_ITEM},
				{
					"fieldname": "amended_from",
					"fieldtype": "Link",
					"label": "Amended From",
					"options": ORDER,
					"read_only": 1,
					"no_copy": 1,
				},
			],
			"permissions": [
				_perm("System Manager", write=1, create=1, delete=1, submit=1, cancel=1, amend=1),
				{"role": "System Manager", "permlevel": 1, "read": 1, "write": 1},
				_perm(SALES_ROLE, write=1, create=1, delete=1, submit=1, cancel=1, amend=1),
				_perm(VIEWER_ROLE),
				_perm(CAPPED_ROLE, write=1, create=1),
			],
		},
	)
	_ensure(
		"DocType",
		REQUEST,
		{
			"name": REQUEST,
			"module": "MCP Connector",
			"custom": 1,
			"autoname": "hash",
			"fields": [{"fieldname": "subject", "fieldtype": "Data", "label": "Subject", "in_list_view": 1}],
			"permissions": [
				_perm("System Manager", write=1, create=1, delete=1),
				_perm(SALES_ROLE, write=1, create=1, delete=1),
			],
		},
	)


def ensure_workflow():
	for state in ("Pending", "Approved", "Rejected"):
		_ensure("Workflow State", state, {"workflow_state_name": state})
	for action in ("Approve", "Reject"):
		_ensure("Workflow Action Master", action, {"workflow_action_name": action})
	_ensure(
		"Workflow",
		WORKFLOW,
		{
			"workflow_name": WORKFLOW,
			"document_type": REQUEST,
			"is_active": 1,
			"send_email_alert": 0,
			"workflow_state_field": "workflow_state",
			"states": [
				{"state": "Pending", "doc_status": "0", "allow_edit": SALES_ROLE},
				{"state": "Approved", "doc_status": "0", "allow_edit": SALES_ROLE},
				{"state": "Rejected", "doc_status": "0", "allow_edit": SALES_ROLE},
			],
			"transitions": [
				{"state": "Pending", "action": "Approve", "next_state": "Approved", "allowed": SALES_ROLE, "allow_self_approval": 1},
				{"state": "Pending", "action": "Reject", "next_state": "Rejected", "allowed": SALES_ROLE, "allow_self_approval": 1},
			],
		},
	)


def ensure_report():
	_ensure(
		"Report",
		REPORT,
		{
			"report_name": REPORT,
			"ref_doctype": ORDER,
			"report_type": "Query Report",
			"is_standard": "No",
			"module": "MCP Connector",
			"query": "select name as `Order:Link/MCP Test Order:140`, company as `Company::120`, amount as `Amount:Currency:100` "
			"from `tabMCP Test Order` where company = %(company)s order by name",
			"filters": [{"fieldname": "company", "label": "Company", "fieldtype": "Data", "mandatory": 1}],
			"roles": [{"role": SALES_ROLE}, {"role": VIEWER_ROLE}, {"role": "System Manager"}],
		},
	)


def ensure_builder_report():
	import json

	_ensure(
		"Report",
		BUILDER_REPORT,
		{
			"report_name": BUILDER_REPORT,
			"ref_doctype": ORDER,
			"report_type": "Report Builder",
			"is_standard": "No",
			"module": "MCP Connector",
			"json": json.dumps(
				{
					"columns": [["name", ORDER], ["customer", ORDER], ["company", ORDER]],
					"filters": [],
					"order_by": f"`tab{ORDER}`.`name` asc",
				}
			),
		},
	)


def ensure_server_script():
	_ensure(
		"Server Script",
		SERVER_SCRIPT,
		{
			"name": SERVER_SCRIPT,
			"script_type": "API",
			"api_method": SERVER_SCRIPT_METHOD,
			"script": 'frappe.response["message"] = {"echo": frappe.form_dict.get("text"), "user": frappe.session.user}',
		},
	)


def ensure_all():
	frappe.set_user("Administrator")
	ensure_roles()
	ensure_users()
	ensure_system_manager()
	ensure_doctypes()
	ensure_workflow()
	ensure_report()
	ensure_builder_report()
	if frappe.conf.get("server_script_enabled"):
		ensure_server_script()
	frappe.db.commit()


def make_order(customer="Alpha", company="ACME", amount=100, items=None, **extra):
	doc = frappe.get_doc(
		{
			"doctype": ORDER,
			"customer": customer,
			"company": company,
			"amount": amount,
			"secret_note": f"secret of {customer}",
			"items": items if items is not None else [{"item": "Widget", "qty": 1}, {"item": "Gadget", "qty": 2}],
			**extra,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc
