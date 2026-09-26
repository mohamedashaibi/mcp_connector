import json
from types import SimpleNamespace

import frappe
from frappe.tests.utils import FrappeTestCase

from mcp_connector.api.mcp import build_handler, build_session
from mcp_connector.domain.action_types import ACTION_TYPE_FIELD, OPERATION_FIELD
from mcp_connector.infrastructure.policy_source import clear_cache
from mcp_connector.infrastructure.settings import load_settings
from mcp_connector.tests import fixtures


class McpTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		fixtures.ensure_all()
		clear_cache()
		cls.addClassCleanup(clear_cache)  # runs before FrappeTestCase's rollback

	def setUp(self):
		frappe.set_user("Administrator")
		clear_cache()

	def tearDown(self):
		frappe.set_user("Administrator")

	@staticmethod
	def make_profile(name, *, roles=(), users=(), types=("Read",), actions=None, doctypes=(), reports=(), enabled=1):
		if frappe.db.exists("MCP Profile", name):
			frappe.delete_doc("MCP Profile", name, ignore_permissions=True)
		if actions is None:
			actions = frappe.get_all("MCP Action", filters={"is_standard": 1}, pluck="name")
		doc = frappe.get_doc(
			{
				"doctype": "MCP Profile",
				"profile_name": name,
				"enabled": enabled,
				"roles": [{"role": r} for r in roles],
				"users": [{"user": u} for u in users],
				"actions": [{"action": a} for a in actions],
				"doctypes": [
					{
						"document_type": row["doctype"],
						**{OPERATION_FIELD[op]: 1 for op in row.get("ops", ("Read",))},
						"field_mode": row.get("field_mode", "All Fields"),
						"field_list": row.get("field_list", ""),
						"scope_filters": row.get("scope", ""),
						"notes": row.get("notes", ""),
					}
					for row in doctypes
				],
				"reports": [
					{"report": row["report"], "fixed_filters": row.get("fixed", ""), "notes": row.get("notes", "")}
					for row in reports
				],
				**{ACTION_TYPE_FIELD[t]: 1 for t in types},
			}
		)
		doc.insert(ignore_permissions=True)
		clear_cache()
		return doc

	@staticmethod
	def rpc(user, method, params=None, protocol="2025-06-18"):
		frappe.set_user(user)
		try:
			session = build_session(user, load_settings(), protocol)
			return build_handler().execute({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, session)
		finally:
			frappe.set_user("Administrator")

	def tools(self, user):
		return {t["name"]: t for t in self.rpc(user, "tools/list")["result"]["tools"]}

	def call(self, user, tool, /, **arguments):
		response = self.rpc(user, "tools/call", {"name": tool, "arguments": arguments})
		if "error" in response:
			return SimpleNamespace(rpc_error=response["error"], error=True, text=response["error"]["message"], data=None)
		result = response["result"]
		text = result["content"][0]["text"]
		return SimpleNamespace(rpc_error=None, error=result["isError"], text=text, data=None if result["isError"] else json.loads(text))

	@staticmethod
	def last_audit():
		name = frappe.get_all("MCP Audit Log", order_by="creation desc, name desc", limit=1, pluck="name")
		return frappe.get_doc("MCP Audit Log", name[0]) if name else None
