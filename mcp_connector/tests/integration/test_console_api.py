"""The console's whitelisted endpoints: role checks, strict payloads, round trips."""

import json

import frappe

from mcp_connector.api import console
from mcp_connector.tests import fixtures
from mcp_connector.tests.integration.base import McpTestCase

F = fixtures
SYSMAN = F.SYSTEM_MANAGER_USER


class TestConsoleApi(McpTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.make_profile(
			"Console Sales",
			roles=[F.SALES_ROLE],
			types=("Read", "Create", "Report"),
			doctypes=[{"doctype": F.ORDER, "ops": ("Read", "Create")}],
			reports=[{"report": F.REPORT}],
		)
		cls.make_profile(
			"Console Viewer",
			roles=[F.VIEWER_ROLE],
			types=("Read", "Create"),
			doctypes=[{"doctype": F.ORDER, "ops": ("Read", "Create")}],
		)

	def payload(self, **overrides):
		data = {
			"original_name": "",
			"profile_name": "Console Built",
			"enabled": True,
			"description": "Made through the console API",
			"roles": [F.SALES_ROLE],
			"users": [],
			"action_types": ["Read", "Report"],
			"actions": ["list_documents", "get_document", "run_report"],
			"doctypes": [
				{
					"doctype": F.ORDER,
					"operations": ["Read"],
					"field_mode": "Only Listed Fields",
					"field_list": ["customer", "amount"],
					"scope": [["company", "=", "ACME"]],
					"notes": "Look but do not touch",
				}
			],
			"reports": [{"report": F.REPORT, "fixed_filters": {"company": "ACME"}, "notes": ""}],
		}
		data.update(overrides)
		return data

	def test_bulk_picker_catalogs(self):
		doctypes = {d["name"]: d for d in console.doctype_catalog()}
		self.assertIn(F.ORDER, doctypes)
		self.assertNotIn("Website Settings", [d for d in doctypes if doctypes[d]["writable"]])
		self.assertTrue(doctypes[F.ORDER]["is_submittable"] and doctypes[F.ORDER]["writable"])
		self.assertEqual(doctypes[F.ORDER]["module"], "MCP Connector")
		self.assertFalse(doctypes["User"]["writable"])
		for hidden in ("MCP Profile", "MCP Audit Log", "System Console", F.ORDER_ITEM):
			self.assertNotIn(hidden, doctypes)
		reports = {r["name"]: r for r in console.report_catalog()}
		self.assertEqual(reports[F.REPORT]["report_type"], "Query Report")

	def test_only_the_administrator_may_use_the_console(self):
		self.assertIn("profiles", console.bootstrap())  # positive control
		for user in (SYSMAN, F.SALES_USER):
			frappe.set_user(user)
			for call in (
				console.bootstrap,
				console.doctype_catalog,
				lambda: console.get_profile("Console Sales"),
				lambda: console.inspect_user(F.SALES_USER),
				lambda: console.save_settings("{}"),
				lambda: console.generate_keys(F.SALES_USER),
				console.claude_connector,
				console.create_claude_connector,
			):
				with self.assertRaises(frappe.PermissionError, msg=user):
					call()
		frappe.set_user("Administrator")

	def test_system_manager_cannot_see_workspace_page_or_records(self):
		from frappe.desk.desktop import get_workspace_sidebar_items

		sidebar = lambda: [p.get("name") for p in get_workspace_sidebar_items()["pages"]]
		self.assertIn("MCP Connector", sidebar())  # positive control: the Administrator sees it
		self.assertTrue(frappe.get_doc("Page", "mcp-console").is_permitted())
		profile = frappe.get_doc("MCP Profile", "Console Sales")
		frappe.set_user(SYSMAN)
		try:
			self.assertNotIn("MCP Connector", sidebar())
			self.assertFalse(frappe.get_doc("Page", "mcp-console").is_permitted())
			for doctype in ("MCP Profile", "MCP Settings", "MCP Action", "MCP Audit Log"):
				self.assertFalse(frappe.has_permission(doctype, "read"), doctype)
			self.assertFalse(frappe.has_permission("MCP Profile", "write", doc=profile))
		finally:
			frappe.set_user("Administrator")

	def test_widening_role_permissions_does_not_let_system_managers_in(self):
		profile = frappe.get_doc("MCP Profile", "Console Sales")
		frappe.get_doc(
			{
				"doctype": "Custom DocPerm",
				"parent": "MCP Profile",
				"parenttype": "DocType",
				"parentfield": "permissions",
				"role": "System Manager",
				"permlevel": 0,
				"read": 1,
				"write": 1,
			}
		).insert(ignore_permissions=True)
		frappe.clear_cache(doctype="MCP Profile")
		frappe.set_user(SYSMAN)
		try:
			self.assertFalse(frappe.has_permission("MCP Profile", "read", doc=profile))
			self.assertFalse(frappe.has_permission("MCP Profile", "write", doc=profile))
			self.assertEqual(frappe.get_list("MCP Profile"), [])
		finally:
			frappe.set_user("Administrator")
			frappe.db.delete("Custom DocPerm", {"parent": "MCP Profile"})
			frappe.clear_cache(doctype="MCP Profile")

	def test_bootstrap(self):
		data = console.bootstrap()
		self.assertTrue(data["endpoint"].endswith("/api/method/mcp_connector.api.mcp.endpoint"))
		self.assertIn("Console Sales", [p["name"] for p in data["profiles"]])
		self.assertEqual(len([a for a in data["actions"] if a["is_standard"]]), 14)
		self.assertIn("MCP Profile", data["protected"]["never"])
		self.assertEqual(data["dashboard"]["hours"], 24)

	def test_save_profile_round_trip_and_rename(self):
		saved = console.save_profile(json.dumps(self.payload()))
		self.assertEqual(saved["name"], "Console Built")
		row = saved["doctypes"][0]
		self.assertEqual((row["field_mode"], row["field_list"]), ("Only Listed Fields", ["amount", "customer"]))
		self.assertEqual(row["scope"], [["company", "=", "ACME"]])
		self.assertEqual(saved["reports"][0]["fixed_filters"], {"company": "ACME"})
		self.assertGreaterEqual(saved["matched_users"], 1)

		renamed = console.save_profile(json.dumps(self.payload(original_name="Console Built", profile_name="Console Renamed")))
		self.assertEqual(renamed["name"], "Console Renamed")
		self.assertFalse(frappe.db.exists("MCP Profile", "Console Built"))

	def test_save_profile_rejects_unexpected_or_invalid_input(self):
		for bad in (
			self.payload(ignore_permissions=True),
			self.payload(action_types=["Read", "Root"]),
			self.payload(doctypes=[{"doctype": F.ORDER, "operations": ["Read"], "sql": "drop"}]),
			self.payload(doctypes=[{"doctype": "MCP Settings", "operations": ["Read"]}]),
			self.payload(enabled="yes"),
		):
			with self.assertRaises(frappe.ValidationError):
				console.save_profile(json.dumps(bad))

	def test_duplicate_and_delete(self):
		created = console.duplicate_profile("Console Sales", "Console Sales Copy")
		self.assertEqual(created, "Console Sales Copy")
		self.assertEqual(frappe.db.get_value("MCP Profile", created, "enabled"), 0)
		console.delete_profile(created)
		self.assertFalse(frappe.db.exists("MCP Profile", created))

	def test_inspector_explains_access(self):
		sales = console.inspect_user(F.SALES_USER)
		self.assertTrue(sales["has_access"])
		self.assertEqual(sales["profiles"][0]["reasons"], [{"type": "role", "value": F.SALES_ROLE}])
		order_row = next(d for d in sales["doctypes"] if d["doctype"] == F.ORDER)
		self.assertEqual((order_row["operations"]["Read"], order_row["operations"]["Create"]), ("allowed", "allowed"))
		self.assertEqual(sales["reports"], [{"report": F.REPORT, "permitted": True}])

		viewer = console.inspect_user(F.VIEWER_USER)
		viewer_row = next(d for d in viewer["doctypes"] if d["doctype"] == F.ORDER)
		self.assertEqual(viewer_row["operations"]["Create"], "erp_denied")
		self.assertNotIn("create_document", [t["name"] for t in viewer["tools"]])

		orphan = console.inspect_user(F.ORPHAN_USER)
		self.assertFalse(orphan["has_access"])

	def test_preview_profile(self):
		preview = console.preview_profile("Console Sales")
		names = {t["name"]: t for t in preview["tools"]}
		self.assertIn("create_document", names)
		self.assertEqual(names["run_report"]["reports"], [F.REPORT])

	def test_actions_endpoints(self):
		result = console.save_action(
			json.dumps(
				{
					"is_new": True,
					"tool_name": "console_ping",
					"title": "Ping",
					"action_type": "Custom",
					"enabled": True,
					"description": "Ping the server",
					"method": "frappe.ping",
					"input_schema": None,
					"read_only_hint": True,
					"destructive_hint": False,
				}
			)
		)
		self.assertIn("console_ping", [a["name"] for a in result["actions"]])
		standard = next(a for a in result["actions"] if a["name"] == "get_document")
		payload = {k: standard[k] for k in ("title", "action_type", "enabled", "description", "method", "input_schema", "read_only_hint", "destructive_hint")}
		payload.update({"is_new": False, "tool_name": "get_document", "description": "Rewritten", "method": ""})
		updated = next(a for a in console.save_action(json.dumps(payload))["actions"] if a["name"] == "get_document")
		self.assertEqual((updated["description"], updated["action_type"]), ("Rewritten", "Read"))
		console.delete_action("console_ping")
		self.assertFalse(frappe.db.exists("MCP Action", "console_ping"))

	def test_settings_round_trip_and_validation(self):
		current = console.bootstrap()["settings"]
		saved = console.save_settings(json.dumps({**current, "server_name": "Tested MCP", "allowed_origins": ["http://localhost:6274"]}))
		self.assertEqual((saved["server_name"], saved["allowed_origins"]), ("Tested MCP", ["http://localhost:6274"]))
		with self.assertRaises(frappe.ValidationError):
			console.save_settings(json.dumps({**current, "default_page_size": 400, "max_page_size": 10}))
		with self.assertRaises(frappe.ValidationError):
			console.save_settings(json.dumps({**current, "allowed_origins": ["javascript:alert(1)"]}))

	def test_logs_and_dashboard(self):
		self.call(F.SALES_USER, "list_documents", doctype=F.ORDER)
		logs = console.logs(json.dumps({"user": F.SALES_USER, "hours": 1}))
		self.assertTrue(logs["rows"])
		self.assertEqual(logs["rows"][0]["tool"], "list_documents")
		detail = console.log_detail(logs["rows"][0]["name"])
		self.assertEqual(detail["user"], F.SALES_USER)
		self.assertTrue(detail["arguments"])
		dash = console.dashboard(24)
		self.assertGreaterEqual(dash["total"], 1)
		self.assertEqual(len(dash["series"]["labels"]), 24)
		with self.assertRaises(frappe.ValidationError):
			console.logs(json.dumps({"status": "Hacked"}))


class TestClaudeConnector(McpTestCase):
	def test_console_creates_one_claude_client_with_exactly_claudes_callbacks(self):
		from mcp_connector.domain.oauth import CLAUDE_CLIENT_NAME, CLAUDE_REDIRECT_URIS

		frappe.set_user("Administrator")
		self.assertIsNone(console.claude_connector()["client"])  # positive control: nothing yet
		created = console.create_claude_connector()
		self.addCleanup(frappe.delete_doc, "OAuth Client", created["client"]["name"], ignore_permissions=True, force=True)
		client = created["client"]
		self.assertEqual(client["redirect_uris"], list(CLAUDE_REDIRECT_URIS))
		self.assertTrue(client["client_id"] and client["client_secret"])
		self.assertTrue(created["connector_url"].endswith("/api/method/mcp_connector.api.mcp.endpoint"))
		self.assertEqual(console.create_claude_connector()["client"]["client_id"], client["client_id"])
		self.assertEqual(console.claude_connector()["client"]["client_id"], client["client_id"])
		self.assertEqual(frappe.db.count("OAuth Client", {"app_name": CLAUDE_CLIENT_NAME}), 1)
		doc = frappe.get_doc("OAuth Client", client["name"])
		self.assertEqual((doc.grant_type, doc.response_type, doc.skip_authorization), ("Authorization Code", "Code", 0))

	def test_discovery_documents_are_served_only_at_their_paths(self):
		from mcp_connector.api.discovery import OAuthDiscoveryRenderer

		server = json.loads(OAuthDiscoveryRenderer(".well-known/oauth-authorization-server", 200).render().get_data())
		self.assertEqual(server["code_challenge_methods_supported"], ["S256"])
		resource = OAuthDiscoveryRenderer(".well-known/oauth-protected-resource/api/method/mcp_connector.api.mcp.endpoint", 200)
		self.assertTrue(resource.can_render())
		self.assertEqual(json.loads(resource.render().get_data())["authorization_servers"], [server["issuer"]])
		for path in (".well-known/openid-configuration", ".well-known/oauth-protected-resource/api/method/frappe.ping", "login"):
			self.assertFalse(OAuthDiscoveryRenderer(path, 200).can_render(), path)
