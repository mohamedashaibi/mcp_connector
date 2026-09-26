"""The connector's configuration and audit trail belong to the Administrator alone.

DocType permissions already name only the Administrator role (which Frappe never
assigns to another user). These hooks make that hold even if someone with
System Manager adds role permissions for the MCP DocTypes later.
"""

import frappe

ADMINISTRATOR = "Administrator"
ADMIN_ONLY_DOCTYPES = ("MCP Settings", "MCP Profile", "MCP Action", "MCP Audit Log")


def is_administrator(user=None) -> bool:
	return (user or frappe.session.user) == ADMINISTRATOR


def administrator_only(doc=None, ptype=None, user=None):
	"""has_permission hook: refuse everyone but the Administrator, whatever the role permissions say."""
	return None if is_administrator(user) else False


def administrator_only_query(user=None) -> str:
	"""permission_query_conditions hook: list views return nothing for anyone else."""
	return "" if is_administrator(user) else "1=0"
