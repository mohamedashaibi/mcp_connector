"""Every MCP tool against a real Frappe site, as real users with real roles."""

import frappe

from mcp_connector.domain.audit import REDACTED
from mcp_connector.domain.jsonrpc import INVALID_PARAMS
from mcp_connector.tests import fixtures
from mcp_connector.tests.integration.base import McpTestCase

F = fixtures


class TestMcpTools(McpTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		frappe.get_doc(
			{"doctype": "MCP Action", "tool_name": "server_ping", "action_type": "Custom", "description": "Ping", "method": "frappe.ping", "read_only_hint": 1}
		).insert()
		custom = ["server_ping"]
		if F.server_scripts_enabled():
			frappe.get_doc(
				{
					"doctype": "MCP Action",
					"tool_name": "echo_text",
					"action_type": "Custom",
					"description": "Echo text back",
					"method": F.SERVER_SCRIPT_METHOD,
					"input_schema": '{"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}',
				}
			).insert()
			custom.append("echo_text")
		standard = frappe.get_all("MCP Action", filters={"is_standard": 1}, pluck="name")
		cls.make_profile(
			"Test Sales Agent",
			roles=[F.SALES_ROLE],
			types=("Read", "Create", "Update", "Delete", "Submit", "Cancel", "Report", "Custom"),
			actions=standard + custom,
			doctypes=[
				{
					"doctype": F.ORDER,
					"ops": ("Read", "Create", "Update", "Delete", "Submit", "Cancel"),
					"field_mode": "Exclude Listed Fields",
					"field_list": "secret_note",
					"scope": '{"company": "ACME"}',
					"notes": "ACME orders only",
				},
				{"doctype": F.REQUEST, "ops": ("Read", "Update")},
			],
			reports=[
				{"report": F.REPORT, "fixed": '{"company": "ACME"}', "notes": "Pinned to ACME"},
				{"report": F.BUILDER_REPORT},
			],
		)
		cls.make_profile(
			"Test Viewer",
			roles=[F.VIEWER_ROLE],
			types=("Read", "Create"),
			actions=["list_documents", "get_document", "create_document"],
			doctypes=[{"doctype": F.ORDER, "ops": ("Read", "Create")}],
		)
		cls.acme1 = F.make_order("Alpha", "ACME", 100, internal_code="IC-1").name
		cls.acme2 = F.make_order("Beta", "ACME", 250).name
		cls.globex = F.make_order("Gamma", "Globex", 900).name

	# -- discovery -----------------------------------------------------------
	def test_tools_list_reflects_profile(self):
		tools = self.tools(F.SALES_USER)
		for name in ("list_documents", "create_document", "submit_document", "run_report", "server_ping"):
			self.assertIn(name, tools)
		self.assertEqual(tools["list_documents"]["inputSchema"]["properties"]["doctype"]["enum"], [F.ORDER, F.REQUEST])
		self.assertEqual(tools["delete_document"]["inputSchema"]["properties"]["doctype"]["enum"], [F.ORDER])
		self.assertEqual(tools["run_report"]["inputSchema"]["properties"]["report_name"]["enum"], sorted([F.BUILDER_REPORT, F.REPORT]))

	def test_erp_role_permissions_narrow_the_profile(self):
		tools = self.tools(F.VIEWER_USER)
		self.assertIn("list_documents", tools)
		self.assertNotIn("create_document", tools)  # profile grants Create, the ERPNext role does not

	def test_user_without_profile(self):
		from mcp_connector.api.mcp import build_session
		from mcp_connector.infrastructure.settings import load_settings

		self.assertTrue(build_session(F.ORPHAN_USER, load_settings()).policy.is_empty)

	def test_initialize(self):
		result = self.rpc(F.SALES_USER, "initialize", {"protocolVersion": "2025-06-18"})["result"]
		self.assertEqual(result["protocolVersion"], "2025-06-18")
		self.assertIn(F.SALES_USER, result["instructions"])

	def test_list_doctypes_and_describe(self):
		rows = {r["doctype"]: r for r in self.call(F.SALES_USER, "list_doctypes").data["doctypes"]}
		self.assertEqual(rows[F.ORDER]["operations"], ["Read", "Create", "Update", "Delete", "Submit", "Cancel"])
		self.assertEqual(rows[F.ORDER]["notes"], "ACME orders only")
		described = self.call(F.SALES_USER, "describe_doctype", doctype=F.ORDER).data
		names = [f["fieldname"] for f in described["fields"]]
		self.assertIn("customer", names)
		self.assertNotIn("secret_note", names)  # profile hides it
		self.assertNotIn("internal_code", names)  # permlevel 1: the role cannot read it
		self.assertEqual([f["fieldname"] for f in described["child_tables"]["items"]["fields"]], ["item", "qty", "rate"])

	# -- reads ---------------------------------------------------------------
	def test_list_respects_scope(self):
		data = self.call(F.SALES_USER, "list_documents", doctype=F.ORDER, fields=["name", "customer", "company"], limit=50).data
		names = {r["name"] for r in data["data"]}
		self.assertIn(self.acme1, names)
		self.assertIn(self.acme2, names)
		self.assertNotIn(self.globex, names)
		self.assertTrue(all(r["company"] == "ACME" for r in data["data"]))

	def test_hidden_fields_cannot_be_read_filtered_or_sorted(self):
		self.assertTrue(self.call(F.SALES_USER, "list_documents", doctype=F.ORDER, fields=["secret_note"]).error)
		self.assertTrue(self.call(F.SALES_USER, "list_documents", doctype=F.ORDER, filters={"secret_note": ["like", "%"]}).error)
		self.assertTrue(self.call(F.SALES_USER, "list_documents", doctype=F.ORDER, order_by="internal_code asc").error)

	def test_filters_search_sort_and_count(self):
		data = self.call(F.SALES_USER, "list_documents", doctype=F.ORDER, filters=[["amount", ">", 150]], fields=["name"]).data
		self.assertIn(self.acme2, [r["name"] for r in data["data"]])
		self.assertNotIn(self.acme1, [r["name"] for r in data["data"]])
		found = self.call(F.SALES_USER, "list_documents", doctype=F.ORDER, search="Beta").data["data"]
		self.assertEqual([r["name"] for r in found], [self.acme2])
		ordered = self.call(F.SALES_USER, "list_documents", doctype=F.ORDER, fields=["name", "amount"], order_by="amount desc", limit=50).data["data"]
		self.assertEqual([r["amount"] for r in ordered], sorted((r["amount"] for r in ordered), reverse=True))
		count = self.call(F.SALES_USER, "count_documents", doctype=F.ORDER).data["count"]
		self.assertEqual(count, frappe.db.count(F.ORDER, {"company": "ACME"}))

	def test_get_document_projection(self):
		doc = self.call(F.SALES_USER, "get_document", doctype=F.ORDER, name=self.acme1).data
		self.assertEqual(doc["customer"], "Alpha")
		self.assertNotIn("secret_note", doc)
		self.assertNotIn("internal_code", doc)
		self.assertEqual([r["item"] for r in doc["items"]], ["Widget", "Gadget"])
		self.assertTrue(all("parent" not in r for r in doc["items"]))

	def test_out_of_scope_record_is_indistinguishable_from_missing(self):
		hidden = self.call(F.SALES_USER, "get_document", doctype=F.ORDER, name=self.globex)
		missing = self.call(F.SALES_USER, "get_document", doctype=F.ORDER, name="does-not-exist")
		self.assertTrue(hidden.error and missing.error)
		self.assertEqual(hidden.text.replace(self.globex, "X"), missing.text.replace("does-not-exist", "X"))

	# -- writes --------------------------------------------------------------
	def test_create_drops_read_only_child_fields(self):
		result = self.call(
			F.SALES_USER,
			"create_document",
			doctype=F.ORDER,
			data={"customer": "Delta", "company": "ACME", "items": [{"item": "Bolt", "qty": 3, "rate": 999}]},
		)
		self.assertFalse(result.error, result.text)
		created = frappe.get_doc(F.ORDER, result.data["name"])
		self.assertEqual(created.owner, F.SALES_USER)
		self.assertEqual((created.items[0].item, created.items[0].qty, created.items[0].rate), ("Bolt", 3, 0))
		self.assertEqual(self.last_audit().status, "Success")

	def test_create_outside_scope_is_rolled_back(self):
		before = frappe.db.count(F.ORDER)
		result = self.call(F.SALES_USER, "create_document", doctype=F.ORDER, data={"customer": "Evil", "company": "Globex"})
		self.assertTrue(result.error)
		self.assertIn("outside your access scope", result.text)
		self.assertEqual(frappe.db.count(F.ORDER), before)
		self.assertFalse(frappe.db.exists(F.ORDER, {"customer": "Evil"}))
		self.assertEqual(self.last_audit().status, "Denied")

	def test_writing_hidden_or_protected_fields_is_refused(self):
		for data in ({"customer": "X", "company": "ACME", "secret_note": "x"}, {"customer": "X", "company": "ACME", "internal_code": "x"}):
			result = self.call(F.SALES_USER, "create_document", doctype=F.ORDER, data=data)
			self.assertTrue(result.error, data)

	def test_erp_validation_errors_surface(self):
		result = self.call(F.SALES_USER, "create_document", doctype=F.ORDER, data={"company": "ACME"})
		self.assertTrue(result.error)
		self.assertIn("Customer", result.text)
		self.assertEqual(self.last_audit().status, "Error")

	def test_update_merges_child_rows(self):
		order = F.make_order("Merge", "ACME")
		widget, gadget = order.items[0].name, order.items[1].name
		result = self.call(
			F.SALES_USER,
			"update_document",
			doctype=F.ORDER,
			name=order.name,
			data={"amount": 555, "items": [{"name": widget, "qty": 9}, {"item": "Sprocket", "qty": 4}]},
		)
		self.assertFalse(result.error, result.text)
		order.reload()
		self.assertEqual(order.amount, 555)
		rows = {r.item: r for r in order.items}
		self.assertEqual(set(rows), {"Widget", "Sprocket"})
		self.assertEqual(rows["Widget"].name, widget)
		self.assertEqual(rows["Widget"].qty, 9)
		self.assertFalse(frappe.db.exists(F.ORDER_ITEM, gadget))

	def test_update_cannot_move_record_out_of_scope(self):
		result = self.call(F.SALES_USER, "update_document", doctype=F.ORDER, name=self.acme2, data={"company": "Globex"})
		self.assertTrue(result.error)
		self.assertEqual(frappe.db.get_value(F.ORDER, self.acme2, "company"), "ACME")

	def test_submit_cancel_and_delete(self):
		order = F.make_order("Lifecycle", "ACME")
		submitted = self.call(F.SALES_USER, "submit_document", doctype=F.ORDER, name=order.name)
		self.assertFalse(submitted.error, submitted.text)
		self.assertEqual(frappe.db.get_value(F.ORDER, order.name, "docstatus"), 1)
		self.assertTrue(self.call(F.SALES_USER, "delete_document", doctype=F.ORDER, name=order.name).error)
		cancelled = self.call(F.SALES_USER, "cancel_document", doctype=F.ORDER, name=order.name)
		self.assertFalse(cancelled.error, cancelled.text)
		self.assertEqual(frappe.db.get_value(F.ORDER, order.name, "docstatus"), 2)
		draft = F.make_order("Disposable", "ACME")
		deleted = self.call(F.SALES_USER, "delete_document", doctype=F.ORDER, name=draft.name)
		self.assertFalse(deleted.error, deleted.text)
		self.assertFalse(frappe.db.exists(F.ORDER, draft.name))

	def test_workflow_actions(self):
		frappe.set_user(F.SALES_USER)
		request = frappe.get_doc({"doctype": F.REQUEST, "subject": "Budget increase"}).insert()
		frappe.set_user("Administrator")
		doc = self.call(F.SALES_USER, "get_document", doctype=F.REQUEST, name=request.name).data
		self.assertEqual(doc["_workflow"]["state"], "Pending")
		self.assertEqual(sorted(a["action"] for a in doc["_workflow"]["actions"]), ["Approve", "Reject"])
		bad = self.call(F.SALES_USER, "apply_workflow_action", doctype=F.REQUEST, name=request.name, action="Escalate")
		self.assertIn("Available", bad.text)
		ok = self.call(F.SALES_USER, "apply_workflow_action", doctype=F.REQUEST, name=request.name, action="Approve")
		self.assertFalse(ok.error, ok.text)
		self.assertEqual(frappe.db.get_value(F.REQUEST, request.name, "workflow_state"), "Approved")

	# -- reports -------------------------------------------------------------
	def test_reports(self):
		listed = self.call(F.SALES_USER, "list_reports").data["reports"]
		self.assertEqual(sorted(r["report_name"] for r in listed), sorted([F.REPORT, F.BUILDER_REPORT]))
		described = self.call(F.SALES_USER, "describe_report", report_name=F.REPORT).data
		self.assertEqual(described["filters"][0]["fieldname"], "company")
		self.assertEqual(described["fixed_filters"], {"company": "ACME"})
		run = self.call(F.SALES_USER, "run_report", report_name=F.REPORT, filters={"company": "Globex"})
		self.assertFalse(run.error, run.text)
		self.assertEqual(run.data["filters_applied"], {"company": "ACME"})
		self.assertTrue(run.data["rows"])
		# Frappe keys query-report rows by the scrubbed column label.
		self.assertEqual([c["fieldname"] for c in run.data["columns"]], ["order", "company", "amount"])
		self.assertTrue(all(row["company"] == "ACME" for row in run.data["rows"]))
		self.assertNotIn(self.globex, [row["order"] for row in run.data["rows"]])

	def test_report_builder_report(self):
		run = self.call(F.SALES_USER, "run_report", report_name=F.BUILDER_REPORT, filters={"customer": "Alpha"})
		self.assertFalse(run.error, run.text)
		self.assertEqual([c["fieldname"] for c in run.data["columns"]], ["name", "customer", "company"])
		self.assertTrue(run.data["rows"])
		self.assertTrue(all(row["customer"] == "Alpha" for row in run.data["rows"]))
		probe = self.call(F.SALES_USER, "run_report", report_name=F.BUILDER_REPORT, filters={"secret_note": ["like", "%"]})
		self.assertTrue(probe.error)
		self.assertIn("only be filtered on its columns", probe.text)

	# -- custom actions, controls and audit ----------------------------------
	def test_custom_actions(self):
		self.assertEqual(self.call(F.SALES_USER, "server_ping").data["result"], "pong")
		if F.server_scripts_enabled():
			echo = self.call(F.SALES_USER, "echo_text", text="hello").data["result"]
			self.assertEqual(echo, {"echo": "hello", "user": F.SALES_USER})
			self.assertTrue(self.call(F.SALES_USER, "echo_text").error)

	def test_disabled_action_disappears(self):
		frappe.db.set_value("MCP Action", "count_documents", "enabled", 0)
		from mcp_connector.infrastructure.policy_source import clear_cache

		clear_cache()
		try:
			self.assertNotIn("count_documents", self.tools(F.SALES_USER))
			response = self.rpc(F.SALES_USER, "tools/call", {"name": "count_documents", "arguments": {"doctype": F.ORDER}})
			self.assertEqual(response["error"]["code"], INVALID_PARAMS)
		finally:
			frappe.db.set_value("MCP Action", "count_documents", "enabled", 1)
			clear_cache()

	def test_audit_masks_secrets(self):
		self.call(F.SALES_USER, "create_document", doctype=F.ORDER, data={"customer": "S", "company": "ACME", "password": "hunter2"})
		entry = self.last_audit()
		self.assertEqual(entry.user, F.SALES_USER)
		self.assertIn(REDACTED, entry.arguments)
		self.assertNotIn("hunter2", entry.arguments)
		self.assertIn("Test Sales Agent", entry.profiles)


class TestProfileAlternatives(McpTestCase):
	"""Two profiles for one role must not blend: fields of one never apply to rows of the other."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.make_profile(
			"Alt Scoped",
			roles=[F.VIEWER_ROLE],
			actions=["get_document", "list_documents"],
			doctypes=[{"doctype": F.ORDER, "scope": '{"company": "ACME"}'}],
		)
		cls.make_profile(
			"Alt Narrow",
			roles=[F.VIEWER_ROLE],
			actions=["get_document", "list_documents"],
			doctypes=[{"doctype": F.ORDER, "field_mode": "Only Listed Fields", "field_list": "customer"}],
		)
		cls.acme = F.make_order("Alt Acme", "ACME", 111).name
		cls.globex = F.make_order("Alt Globex", "Globex", 999).name

	def test_fields_follow_the_profile_that_covers_the_record(self):
		acme = self.call(F.VIEWER_USER, "get_document", doctype=F.ORDER, name=self.acme).data
		self.assertEqual(acme["amount"], 111)
		globex = self.call(F.VIEWER_USER, "get_document", doctype=F.ORDER, name=self.globex).data
		self.assertEqual(globex["customer"], "Alt Globex")
		self.assertNotIn("amount", globex)
		self.assertNotIn("company", globex)

	def test_listing_hidden_fields_only_returns_rows_of_the_profile_showing_them(self):
		rows = self.call(F.VIEWER_USER, "list_documents", doctype=F.ORDER, fields=["name", "amount"], limit=100).data["data"]
		names = [r["name"] for r in rows]
		self.assertIn(self.acme, names)
		self.assertNotIn(self.globex, names)
		everyone = self.call(F.VIEWER_USER, "list_documents", doctype=F.ORDER, fields=["name", "customer"], limit=100).data["data"]
		self.assertIn(self.globex, [r["name"] for r in everyone])
