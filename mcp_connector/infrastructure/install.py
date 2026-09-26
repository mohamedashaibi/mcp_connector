import frappe

from mcp_connector.domain.catalog import BUILTIN_TOOLS
from mcp_connector.infrastructure.policy_source import clear_cache

LEGACY_MANAGER_ROLE = "MCP Manager"


def after_install():
	sync_standard_actions()
	ensure_settings_defaults()


def after_migrate():
	remove_legacy_manager_role()
	sync_standard_actions()
	ensure_settings_defaults()
	clear_cache()


def remove_legacy_manager_role():
	"""Earlier versions created an 'MCP Manager' role; management is now Administrator-only."""
	if frappe.db.exists("Role", LEGACY_MANAGER_ROLE):
		frappe.db.delete("Has Role", {"role": LEGACY_MANAGER_ROLE})
		frappe.delete_doc("Role", LEGACY_MANAGER_ROLE, ignore_permissions=True, force=True)


def sync_standard_actions():
	"""Create/refresh one MCP Action per built-in tool; keep admin choices (enabled, description)."""
	known = {tool.name for tool in BUILTIN_TOOLS}
	for tool in BUILTIN_TOOLS:
		if frappe.db.exists("MCP Action", tool.name):
			doc = frappe.get_doc("MCP Action", tool.name)
			if (doc.title, doc.action_type, doc.is_standard) == (tool.title, tool.action_type, 1):
				continue
			doc.update({"title": tool.title, "action_type": tool.action_type, "is_standard": 1})
		else:
			doc = frappe.get_doc(
				{
					"doctype": "MCP Action",
					"tool_name": tool.name,
					"title": tool.title,
					"action_type": tool.action_type,
					"is_standard": 1,
					"enabled": 1,
				}
			)
		doc.flags.from_sync = True
		doc.save(ignore_permissions=True)

	for name in frappe.get_all("MCP Action", filters={"is_standard": 1}, pluck="name"):
		if name not in known:
			doc = frappe.get_doc("MCP Action", name)
			doc.flags.from_sync = True
			doc.delete(ignore_permissions=True)
	clear_cache()


def ensure_settings_defaults():
	"""Singles never saved have no values, so write each field's default once."""
	settings = frappe.get_single("MCP Settings")
	changed = False
	for df in settings.meta.fields:
		if df.default is not None and settings.get(df.fieldname) in (None, ""):
			settings.set(df.fieldname, df.default)
			changed = True
	if changed:
		settings.flags.ignore_permissions = True
		settings.save()
