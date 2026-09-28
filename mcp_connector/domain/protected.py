"""DocTypes and methods an MCP client must never touch, whatever a profile says.

Blocking these keeps an AI client (or a prompt injected into one) from
granting itself more access, reading credentials, or running code.
"""

from typing import Optional

from .filters import IDENTIFIER

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
		# HTML / Jinja / script-bearing or sharing and automation rules
		"Website Settings",
		"Portal Settings",
		"Navbar Settings",
		"Web Template",
		"Print Style",
		"Letter Head",
		"Address Template",
		"Terms and Conditions",
		"Translation",
		"DocShare",
		"Assignment Rule",
		"Energy Point Rule",
		"Dashboard Chart Source",
		"Number Card",
		"Workspace",
		"Email Account",
		"Email Domain",
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


# Custom Field is writable, minus the settings that render raw HTML, call server
# methods, run code (eval: in the browser, virtual fields on the server),
# loosen permissions or copy values from records the profile may not reach.
CUSTOM_FIELD = "Custom Field"
_CUSTOM_FIELD_BLOCKED_TYPES = frozenset({"html", "html editor", "button"})
_CUSTOM_FIELD_BLOCKED_FLAGS = ("ignore_xss_filter", "ignore_user_permissions", "is_virtual")

# Frappe resolves DocType names case-insensitively and fixes the case on save.
_NEVER_EXPOSED_FOLDED = frozenset(d.lower() for d in NEVER_EXPOSED)
_NOT_WRITABLE_FOLDED = _NEVER_EXPOSED_FOLDED | frozenset(d.lower() for d in READ_ONLY)


def custom_field_problem(values: dict) -> Optional[str]:
	"""Why a Custom Field holding these values may not be saved over MCP, or None."""

	def text(key):
		value = values.get(key)
		return value.strip() if isinstance(value, str) else ""

	if text("dt").lower() in _NOT_WRITABLE_FOLDED:
		return f"Custom fields cannot be added to {text('dt')} over MCP"
	if text("fieldtype").lower() in _CUSTOM_FIELD_BLOCKED_TYPES:
		return f"{text('fieldtype')} fields cannot be created over MCP"
	if text("options").lower() in _NEVER_EXPOSED_FOLDED:
		return f"A custom field cannot point at {text('options')} over MCP"
	if text("fetch_from"):
		return "'fetch_from' cannot be set over MCP: it copies values from other records"
	for flag in _CUSTOM_FIELD_BLOCKED_FLAGS:
		if values.get(flag):
			return f"'{flag}' cannot be set over MCP"
	for key in values:
		value = text(key)
		if key.endswith("depends_on") and value and not IDENTIFIER.match(value):
			return f"'{key}' may only name a field over MCP; expressions are not allowed"
		if value.lower().startswith("eval:"):
			return f"'{key}' cannot hold an eval: expression over MCP"
	return None
