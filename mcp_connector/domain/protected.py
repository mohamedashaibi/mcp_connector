"""DocTypes and methods an MCP client must never touch, whatever a profile says.

Blocking these keeps an AI client (or a prompt injected into one) from
granting itself more access, reading credentials, or running code.
"""

# The connector's own configuration and audit trail, plus credential stores.
NEVER_EXPOSED = frozenset(
	{
		"MCP Settings",
		"MCP Profile",
		"MCP Profile Role",
		"MCP Profile User",
		"MCP Profile Action",
		"MCP Profile DocType",
		"MCP Profile Report",
		"MCP Action",
		"MCP Audit Log",
		"System Console",
		"OAuth Bearer Token",
		"OAuth Authorization Code",
		"OAuth Client",
		"Token Cache",
		"Connected App",
		"Social Login Key",
		"User Social Login",
		"Access Log",
	}
)

# Readable when a profile grants it, but never created, changed or deleted via
# MCP: these hold executable code, templates, schema or permissions.
READ_ONLY = frozenset(
	{
		"DocType",
		"DocField",
		"DocPerm",
		"Custom DocPerm",
		"Custom Field",
		"Property Setter",
		"Customize Form",
		"Server Script",
		"Client Script",
		"Report",
		"Print Format",
		"Notification",
		"Email Template",
		"Web Page",
		"Web Form",
		"Website Script",
		"Website Theme",
		"Scheduled Job Type",
		"User",
		"Role",
		"Has Role",
		"Role Profile",
		"Module Profile",
		"User Permission",
		"User Type",
		"Custom Role",
		"Page",
		"System Settings",
		"Workflow",
		"Workflow Action Master",
		"Auto Email Report",
		"Data Import",
		"Data Export",
		"Webhook",
		"Package",
		"Installed Applications",
		"Domain Settings",
		"Log Settings",
	}
)

# Custom actions may not point at generic CRUD or auth plumbing: that would
# sidestep the DocType rules a profile defines.
BLOCKED_METHOD_PREFIXES = (
	"frappe.client.",
	"frappe.desk.reportview.",
	"frappe.desk.form.",
	"frappe.desk.query_report.",
	"frappe.desk.search.",
	"frappe.model.",
	"frappe.handler.",
	"frappe.auth.",
	"frappe.www.login.",
	"frappe.integrations.oauth2.",
	"frappe.core.doctype.user.",
	"frappe.core.doctype.data_import.",
	"frappe.core.doctype.system_console.",
	"frappe.core.doctype.server_script.",
	"frappe.utils.print_format.",
	"frappe.desk.doctype.bulk_update.",
	"mcp_connector.",
)


def is_exposable(doctype: str) -> bool:
	return doctype not in NEVER_EXPOSED


def is_writable(doctype: str) -> bool:
	return doctype not in NEVER_EXPOSED and doctype not in READ_ONLY


def is_blocked_method(method: str) -> bool:
	method = (method or "").strip()
	return any(method == p.rstrip(".") or method.startswith(p) for p in BLOCKED_METHOD_PREFIXES)
