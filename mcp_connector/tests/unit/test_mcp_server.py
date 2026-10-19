"""The MCP use cases end to end against in-memory ports."""

import json
import unittest

from mcp_connector.application.dto import Caller, ServerSettings
from mcp_connector.application.mcp_server import (
	LATEST_PROTOCOL_VERSION,
	CallTool,
	HandleMcpMessage,
	McpSession,
	ToolCatalog,
)
from mcp_connector.domain.action_types import CANCEL, CREATE, CUSTOM, DELETE, READ, REPORT, SUBMIT, UPDATE
from mcp_connector.domain.audit import AUDIT_DISABLED, AUDIT_WRITES_AND_FAILURES, DENIED, ERROR, REDACTED, SUCCESS
from mcp_connector.domain.catalog import BUILTIN_TOOLS, OPERATION_TOOLS, SchemaContext
from mcp_connector.domain.errors import InvalidArguments
from mcp_connector.domain.jsonrpc import INVALID_PARAMS, METHOD_NOT_FOUND
from mcp_connector.domain.naming import NAMING_SETTINGS
from mcp_connector.domain.policy import (
	EXCLUDE_LISTED,
	ONLY_LISTED,
	ActionSpec,
	DoctypeRule,
	FieldRule,
	ProfileSpec,
	ReportRule,
	resolve_policy,
)
from mcp_connector.domain.schema import validate_arguments

from .fakes import FakeAudit, FakeGateway, FakeUnitOfWork, field, report

STANDARD_ACTIONS = [ActionSpec(t.name, t.action_type) for t in BUILTIN_TOOLS]
CUSTOM_ACTION = ActionSpec(
	"stock_balance",
	CUSTOM,
	is_standard=False,
	title="Stock balance",
	description="Get stock balance",
	method="erpnext.stock.utils.get_stock_balance",
	input_schema={"type": "object", "properties": {"item_code": {"type": "string"}}, "required": ["item_code"]},
	read_only_hint=True,
)
BLOCKED_ACTION = ActionSpec("sneaky", CUSTOM, is_standard=False, method="frappe.client.get_list")
ALL_ACTIONS = STANDARD_ACTIONS + [CUSTOM_ACTION, BLOCKED_ACTION]
ALL_TOOL_NAMES = frozenset(a.name for a in ALL_ACTIONS)


def build_gateway():
	gw = FakeGateway()
	gw.add_doctype(
		"Customer",
		[
			field("customer_name", in_list_view=True),
			field("territory", "Link", options="Territory", in_list_view=True),
			field("credit_limit", "Currency"),
			field("tax_id"),
			field("portal_password", "Password"),
			field("customer_group", "Link", read_only=True),
		],
		title_field="customer_name",
		search_fields=("territory",),
	)
	gw.add_doctype(
		"Sales Invoice",
		[
			field("customer", "Link", options="Customer", in_list_view=True),
			field("company", "Link", options="Company"),
			field("grand_total", "Currency", read_only=True, in_list_view=True),
			field("items", "Table", options="Sales Invoice Item"),
			field("workflow_state", "Link", read_only=True),
		],
		is_submittable=True,
		child_fields={
			"Sales Invoice Item": (field("item_code", "Link"), field("qty", "Float"), field("amount", "Currency", read_only=True)),
		},
	)
	gw.add_doctype(
		"Purchase Order",
		[field("supplier", "Link"), field("workflow_state", "Link")],
		is_submittable=True,
		has_workflow=True,
		workflow_state_field="workflow_state",
	)
	gw.add_doctype("Selling Settings", [field("cust_master_name", "Select")], issingle=True)
	gw.add_doctype("MCP Profile", [field("profile_name")])
	gw.add_doctype("User", [field("full_name")])

	gw.add_doc("Customer", "CUST-1", customer_name="Alpha", territory="North", credit_limit=5000, tax_id="TX1", portal_password="*****")
	gw.add_doc("Customer", "CUST-2", customer_name="Beta", territory="South", credit_limit=100, tax_id="TX2")
	gw.add_doc("Sales Invoice", "SINV-1", customer="CUST-1", company="ACME", grand_total=100, modified="2024-01-03",
		items=[{"name": "row1", "item_code": "WIDGET", "qty": 1, "amount": 100, "parent": "SINV-1", "doctype": "Sales Invoice Item"}])
	gw.add_doc("Sales Invoice", "SINV-2", customer="CUST-2", company="Globex", grand_total=200, modified="2024-01-02")
	gw.add_doc("Sales Invoice", "SINV-3", customer="CUST-1", company="ACME", grand_total=300, modified="2024-01-01")
	gw.add_doc("Purchase Order", "PO-1", supplier="S1", workflow_state="Pending")
	gw.add_doc("Selling Settings", "Selling Settings", cust_master_name="Customer Name")
	gw.add_doc("MCP Profile", "Admin", profile_name="Admin")
	gw.add_doc("User", "a@x.com", full_name="A")
	gw.workflows[("Purchase Order", "PO-1")] = {
		"state": "Pending",
		"docstatus": 0,
		"actions": [
			{"action": "Approve", "next_state": "Approved", "next_docstatus": 1},
			{"action": "Reject", "next_state": "Rejected", "next_docstatus": 0},
		],
	}
	gw.reports["Sales Register"] = (report("Sales Register", ref_doctype="Sales Invoice", filters=[{"fieldname": "company"}]), [
		{"name": "SINV-1", "company": "ACME"},
		{"name": "SINV-2", "company": "Globex"},
	])
	gw.reports["Secret Report"] = (report("Secret Report"), [])
	gw.reports["Invoice Builder"] = (
		report("Invoice Builder", "Report Builder", ref_doctype="Sales Invoice", filterable=frozenset({"name", "customer"})),
		[{"name": "SINV-1", "customer": "CUST-1"}],
	)
	gw.reports["Forbidden Report"] = (report("Forbidden Report", permitted=False), [])
	gw.methods["erpnext.stock.utils.get_stock_balance"] = lambda item_code: {"item_code": item_code, "qty": 42}
	return gw


SALES_AGENT = ProfileSpec(
	name="Sales Agent",
	roles=frozenset({"Sales User"}),
	action_types=frozenset({READ, CREATE, UPDATE, SUBMIT, REPORT, CUSTOM}),
	actions=ALL_TOOL_NAMES - {"delete_document", "count_documents"},
	doctypes=(
		DoctypeRule("Customer", frozenset({READ, CREATE, UPDATE}), FieldRule(EXCLUDE_LISTED, frozenset({"tax_id", "credit_limit"})), notes="Retail only"),
		DoctypeRule("Sales Invoice", frozenset({READ, CREATE, UPDATE, SUBMIT, DELETE}), scope=(("company", "=", "ACME"),)),
		DoctypeRule("Purchase Order", frozenset({READ, UPDATE})),
		DoctypeRule("Selling Settings", frozenset({READ})),
		DoctypeRule("MCP Profile", frozenset({READ, UPDATE})),
		DoctypeRule("User", frozenset({READ, UPDATE})),
	),
	reports=(ReportRule("Sales Register", {"company": "ACME"}, notes="Company fixed"), ReportRule("Forbidden Report")),
)

AUDITOR = ProfileSpec(
	name="Auditor",
	users=frozenset({"auditor@x.com"}),
	action_types=frozenset({READ}),
	actions=frozenset({"list_documents", "count_documents", "get_document"}),
	doctypes=(DoctypeRule("Sales Invoice", frozenset({READ})),),
)


class Harness:
	def __init__(self, profiles=(SALES_AGENT, AUDITOR), settings=None, user="agent@x.com", roles=("Sales User",), protocol="2025-06-18"):
		self.gateway = build_gateway()
		self.audit = FakeAudit()
		self.uow = FakeUnitOfWork(self.gateway)
		catalog = ToolCatalog(self.gateway)
		self.handler = HandleMcpMessage(catalog, CallTool(catalog, self.gateway, self.audit, self.uow), "1.0.0")
		policy = resolve_policy(user, roles, profiles, ALL_ACTIONS)
		self.session = McpSession(
			caller=Caller(user=user, full_name="Agent Smith", roles=tuple(roles), client_ip="10.0.0.1"),
			policy=policy,
			settings=settings or ServerSettings(default_page_size=2, max_page_size=5, max_report_rows=10),
			actions={a.name: a for a in ALL_ACTIONS},
			protocol_version=protocol,
		)
		self.next_id = 0

	def rpc(self, method, params=None):
		self.next_id += 1
		return self.handler.execute({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}, self.session)

	def tools(self):
		return {t["name"]: t for t in self.rpc("tools/list")["result"]["tools"]}

	def call(self, tool, /, **arguments):
		response = self.rpc("tools/call", {"name": tool, "arguments": arguments})
		if "error" in response:
			return response
		result = response["result"]
		text = result["content"][0]["text"]
		return {"isError": result["isError"], "text": text, "data": None if result["isError"] else json.loads(text), "raw": result}


class TestProtocol(unittest.TestCase):
	def setUp(self):
		self.h = Harness()

	def test_initialize_echoes_supported_version(self):
		result = self.h.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t"}})["result"]
		self.assertEqual(result["protocolVersion"], "2025-03-26")
		self.assertEqual(result["capabilities"], {"tools": {"listChanged": False}})
		self.assertIn("Agent Smith (agent@x.com)", result["instructions"])
		self.assertEqual(result["serverInfo"]["version"], "1.0.0")

	def test_initialize_falls_back_to_latest(self):
		result = self.h.rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]
		self.assertEqual(result["protocolVersion"], LATEST_PROTOCOL_VERSION)

	def test_ping_notification_and_unknown_method(self):
		self.assertEqual(self.h.rpc("ping")["result"], {})
		self.assertIsNone(self.h.handler.execute({"jsonrpc": "2.0", "method": "notifications/initialized"}, self.h.session))
		self.assertEqual(self.h.rpc("resources/list")["error"]["code"], METHOD_NOT_FOUND)

	def test_malformed_message(self):
		response = self.h.handler.execute({"jsonrpc": "2.0", "id": 3, "method": 5}, self.h.session)
		self.assertEqual(response["id"], 3)
		self.assertIn("error", response)


class TestToolsList(unittest.TestCase):
	def test_tools_follow_actions_and_action_types(self):
		tools = Harness().tools()
		self.assertIn("list_documents", tools)
		self.assertIn("submit_document", tools)
		self.assertIn("stock_balance", tools)
		self.assertNotIn("delete_document", tools)  # action not selected, type off
		self.assertNotIn("count_documents", tools)  # action not selected
		self.assertNotIn("cancel_document", tools)  # no doctype grants Cancel

	def test_doctype_enums_only_list_granted_exposable_doctypes(self):
		tools = Harness().tools()
		enum = tools["list_documents"]["inputSchema"]["properties"]["doctype"]["enum"]
		self.assertEqual(enum, ["Customer", "Purchase Order", "Sales Invoice", "Selling Settings", "User"])
		update_enum = tools["update_document"]["inputSchema"]["properties"]["doctype"]["enum"]
		self.assertEqual(update_enum, ["Customer", "Purchase Order", "Sales Invoice"])  # User is read-only, MCP Profile never exposed
		self.assertEqual(tools["submit_document"]["inputSchema"]["properties"]["doctype"]["enum"], ["Sales Invoice"])

	def test_erp_permissions_narrow_the_enum(self):
		h = Harness()
		h.gateway.denied.add(("Customer", "create"))
		enum = h.tools()["create_document"]["inputSchema"]["properties"]["doctype"]["enum"]
		self.assertEqual(enum, ["Sales Invoice"])

	def test_report_enum_skips_reports_erp_forbids(self):
		tools = Harness().tools()
		self.assertEqual(tools["run_report"]["inputSchema"]["properties"]["report_name"]["enum"], ["Sales Register"])

	def test_annotations_and_limits(self):
		tools = Harness().tools()
		self.assertTrue(tools["list_documents"]["annotations"]["readOnlyHint"])
		self.assertFalse(tools["create_document"]["annotations"]["readOnlyHint"])
		self.assertEqual(tools["list_documents"]["inputSchema"]["properties"]["limit"]["maximum"], 5)
		self.assertFalse(tools["list_documents"]["inputSchema"]["additionalProperties"])
		self.assertEqual(tools["stock_balance"]["inputSchema"]["required"], ["item_code"])

	def test_every_array_declares_its_items(self):
		# ChatGPT (OpenAI function calling) refuses a tool whose schema has an array without "items".
		def arrays_without_items(node, path):
			types = node.get("type") if isinstance(node.get("type"), list) else [node.get("type")]
			found = [path] if "array" in types and "items" not in node else []
			for name, sub in (node.get("properties") or {}).items():
				found += arrays_without_items(sub, f"{path}.{name}")
			if isinstance(node.get("items"), dict):
				found += arrays_without_items(node["items"], f"{path}[]")
			return found

		ctx = SchemaContext(doctypes=("Customer",), reports=("Sales Register",))
		for tool in BUILTIN_TOOLS:
			self.assertEqual(arrays_without_items(tool.build_schema(ctx), tool.name), [])

	def test_list_filters_still_validate_after_items(self):
		schema = Harness().tools()["list_documents"]["inputSchema"]
		validate_arguments(schema, {"doctype": "Customer", "filters": [["status", "in", ["A", "B"]], ["Customer", "credit", ">", 1.5], ["x", "=", None]]})
		validate_arguments(schema, {"doctype": "Customer", "filters": {"status": ["between", ["a", "b"]]}})
		with self.assertRaises(InvalidArguments):
			validate_arguments(schema, {"doctype": "Customer", "filters": ["status = A"]})

	def test_description_override(self):
		h = Harness()
		h.session.actions["get_document"] = ActionSpec("get_document", READ, description="Fetch one record. Prefer names from list_documents.")
		self.assertEqual(h.tools()["get_document"]["description"], "Fetch one record. Prefer names from list_documents.")

	def test_user_without_profile_gets_no_tools(self):
		self.assertEqual(Harness(user="nobody@x.com", roles=("Guest",)).tools(), {})


class TestReadTools(unittest.TestCase):
	def setUp(self):
		self.h = Harness()

	def test_list_doctypes(self):
		data = self.h.call("list_doctypes")["data"]
		rows = {r["doctype"]: r for r in data["doctypes"]}
		self.assertEqual(rows["Sales Invoice"]["operations"], [READ, CREATE, UPDATE, SUBMIT])
		self.assertEqual(rows["User"]["operations"], [READ])
		self.assertNotIn("MCP Profile", rows)
		self.assertEqual(rows["Customer"]["notes"], "Retail only")

	def test_describe_doctype_hides_excluded_and_secret_fields(self):
		data = self.h.call("describe_doctype", doctype="Customer")["data"]
		names = [f["fieldname"] for f in data["fields"]]
		self.assertEqual(names, ["customer_name", "territory", "customer_group"])
		invoice = self.h.call("describe_doctype", doctype="Sales Invoice")["data"]
		self.assertEqual([c["fieldname"] for c in invoice["child_tables"]["items"]["fields"]], ["item_code", "qty", "amount"])
		self.assertIn("access_scope", invoice)

	def test_default_list_hides_excluded_fields(self):
		data = self.h.call("list_documents", doctype="Customer")["data"]
		self.assertEqual(data["data"][0].keys(), {"name", "customer_name", "territory", "modified"})

	def test_requesting_hidden_field_fails(self):
		result = self.h.call("list_documents", doctype="Customer", fields=["name", "tax_id"])
		self.assertTrue(result["isError"])
		self.assertIn("tax_id", result["text"])

	def test_filtering_on_hidden_field_fails(self):
		result = self.h.call("list_documents", doctype="Customer", filters={"credit_limit": [">", 1000]})
		self.assertTrue(result["isError"])

	def test_row_scope_is_enforced_on_lists(self):
		data = self.h.call("list_documents", doctype="Sales Invoice", limit=5)["data"]
		self.assertEqual([r["name"] for r in data["data"]], ["SINV-1", "SINV-3"])
		self.assertIn(("company", "=", "ACME"), self.h.gateway.calls[-1][2])

	def test_paging(self):
		first = self.h.call("list_documents", doctype="Customer", limit=1, order_by="name asc")["data"]
		self.assertTrue(first["has_more"])
		second = self.h.call("list_documents", doctype="Customer", limit=1, start=1, order_by="name asc")["data"]
		self.assertEqual(second["data"][0]["name"], "CUST-2")
		self.assertFalse(second["has_more"])

	def test_search(self):
		data = self.h.call("list_documents", doctype="Customer", search="bet")["data"]
		self.assertEqual([r["name"] for r in data["data"]], ["CUST-2"])
		or_fields = [c[0] for c in self.h.gateway.calls[-1][3]]
		self.assertEqual(or_fields, ["name", "customer_name", "territory"])

	def test_get_document_projects_fields(self):
		data = self.h.call("get_document", doctype="Customer", name="CUST-1")["data"]
		self.assertEqual(data["customer_name"], "Alpha")
		for hidden in ("tax_id", "credit_limit", "portal_password"):
			self.assertNotIn(hidden, data)

	def test_get_document_child_rows_are_cleaned(self):
		data = self.h.call("get_document", doctype="Sales Invoice", name="SINV-1")["data"]
		self.assertEqual(data["items"], [{"name": "row1", "item_code": "WIDGET", "qty": 1, "amount": 100}])

	def test_out_of_scope_document_looks_missing(self):
		out_of_scope = self.h.call("get_document", doctype="Sales Invoice", name="SINV-2")
		missing = self.h.call("get_document", doctype="Sales Invoice", name="SINV-404")
		self.assertTrue(out_of_scope["isError"])
		self.assertEqual(out_of_scope["text"].replace("SINV-2", "X"), missing["text"].replace("SINV-404", "X"))

	def test_single_doctype(self):
		data = self.h.call("get_document", doctype="Selling Settings", name="Selling Settings")["data"]
		self.assertEqual(data["cust_master_name"], "Customer Name")
		self.assertTrue(self.h.call("list_documents", doctype="Selling Settings")["isError"])

	def test_workflow_actions_are_listed(self):
		data = self.h.call("get_document", doctype="Purchase Order", name="PO-1")["data"]
		self.assertEqual([a["action"] for a in data["_workflow"]["actions"]], ["Approve", "Reject"])

	def test_doctype_the_erp_role_cannot_read_leaves_the_enum(self):
		self.h.gateway.denied.add(("Customer", "read"))
		result = self.h.call("list_documents", doctype="Customer")
		self.assertTrue(result["isError"])
		self.assertIn("must be one of", result["text"])
		self.assertEqual(self.h.audit.entries[-1].status, ERROR)

	def test_document_level_erp_denial_is_reported_as_denied(self):
		self.h.gateway.doc_denied.add(("Customer", "CUST-1"))
		result = self.h.call("get_document", doctype="Customer", name="CUST-1")
		self.assertTrue(result["isError"])
		self.assertTrue(result["text"].startswith("Permission denied"))
		self.assertEqual(self.h.audit.entries[-1].status, DENIED)

	def test_doctype_outside_enum_is_rejected(self):
		result = self.h.call("list_documents", doctype="MCP Profile")
		self.assertTrue(result["isError"])
		self.assertIn("must be one of", result["text"])

	def test_structured_content_depends_on_protocol(self):
		self.assertIn("structuredContent", self.h.call("list_doctypes")["raw"])
		old = Harness(protocol="2025-03-26")
		self.assertNotIn("structuredContent", old.call("list_doctypes")["raw"])


class TestWriteTools(unittest.TestCase):
	def setUp(self):
		self.h = Harness()

	def test_create_success(self):
		result = self.h.call("create_document", doctype="Customer", data={"customer_name": "Gamma", "territory": "East"})
		self.assertFalse(result["isError"], result["text"])
		self.assertEqual(result["data"]["customer_name"], "Gamma")
		self.assertEqual(self.h.audit.entries[-1].status, SUCCESS)
		self.assertEqual(self.h.audit.entries[-1].document_name, result["data"]["name"])

	def test_create_rejects_hidden_read_only_unknown_and_system_fields(self):
		cases = [
			({"customer_name": "G", "credit_limit": 1}, "Permission denied"),
			({"customer_name": "G", "customer_group": "X"}, "read-only"),
			({"customer_name": "G", "nonexistent": 1}, "has no field"),
			({"customer_name": "G", "owner": "x"}, "managed by the system"),
			({"customer_name": "G", "name": "CUSTOM"}, "cannot be set"),
			({"customer_name": "G", "portal_password": "p"}, "Permission denied"),
			({}, "non-empty"),
		]
		for data, message in cases:
			result = self.h.call("create_document", doctype="Customer", data=data)
			self.assertTrue(result["isError"], data)
			self.assertIn(message, result["text"], data)
		self.assertEqual(len(self.h.gateway.docs["Customer"]), 2)

	def test_create_outside_scope_is_rolled_back(self):
		before = set(self.h.gateway.docs["Sales Invoice"])
		result = self.h.call("create_document", doctype="Sales Invoice", data={"customer": "CUST-1", "company": "Globex"})
		self.assertTrue(result["isError"])
		self.assertIn("outside your access scope", result["text"])
		self.assertEqual(set(self.h.gateway.docs["Sales Invoice"]), before)
		self.assertEqual(self.h.uow.rollbacks, 1)
		self.assertEqual(self.h.audit.entries[-1].status, DENIED)

	def test_child_rows_are_validated(self):
		ok = self.h.call("create_document", doctype="Sales Invoice", data={"customer": "CUST-1", "company": "ACME", "items": [{"item_code": "W", "qty": 2, "amount": 999, "doctype": "x"}]})
		self.assertFalse(ok["isError"], ok["text"])
		created = self.h.gateway.docs["Sales Invoice"][ok["data"]["name"]]
		self.assertEqual(created["items"], [{"item_code": "W", "qty": 2}])  # read-only amount dropped
		bad = self.h.call("create_document", doctype="Sales Invoice", data={"customer": "CUST-1", "company": "ACME", "items": [{"bogus": 1}]})
		self.assertTrue(bad["isError"])
		not_list = self.h.call("create_document", doctype="Sales Invoice", data={"customer": "CUST-1", "company": "ACME", "items": {"item_code": "W"}})
		self.assertTrue(not_list["isError"])

	def test_update_cannot_move_document_out_of_scope(self):
		result = self.h.call("update_document", doctype="Sales Invoice", name="SINV-1", data={"company": "Globex"})
		self.assertTrue(result["isError"])
		self.assertEqual(self.h.gateway.docs["Sales Invoice"]["SINV-1"]["company"], "ACME")

	def test_update_out_of_scope_document_is_not_found(self):
		result = self.h.call("update_document", doctype="Sales Invoice", name="SINV-2", data={"customer": "CUST-1"})
		self.assertTrue(result["isError"])
		self.assertEqual(self.h.gateway.docs["Sales Invoice"]["SINV-2"]["customer"], "CUST-2")

	def test_update_success(self):
		result = self.h.call("update_document", doctype="Customer", name="CUST-2", data={"territory": "West"})
		self.assertFalse(result["isError"], result["text"])
		self.assertEqual(self.h.gateway.docs["Customer"]["CUST-2"]["territory"], "West")

	def test_submit(self):
		result = self.h.call("submit_document", doctype="Sales Invoice", name="SINV-3")
		self.assertFalse(result["isError"], result["text"])
		self.assertEqual(result["data"]["docstatus"], 1)

	def test_erp_validation_error_rolls_back(self):
		result = self.h.call("create_document", doctype="Customer", data={"customer_name": "fail"})
		self.assertTrue(result["isError"])
		self.assertIn("Validation failed", result["text"])
		self.assertEqual(self.h.audit.entries[-1].status, ERROR)

	def test_unexpected_exception_is_contained(self):
		def boom(*a, **k):
			raise RuntimeError("database exploded")

		self.h.gateway.update_document = boom
		result = self.h.call("update_document", doctype="Customer", name="CUST-1", data={"territory": "X"})
		self.assertTrue(result["isError"])
		self.assertNotIn("exploded", result["text"])
		self.assertIn("database exploded", self.h.audit.entries[-1].error)

	def test_erp_messages_are_returned(self):
		self.h.gateway.messages = ["Credit limit crossed"]
		original = self.h.gateway.update_document

		def update(*args):
			self.h.gateway.messages.append("Saved with warning")
			return original(*args)

		self.h.gateway.update_document = update
		data = self.h.call("update_document", doctype="Customer", name="CUST-1", data={"territory": "X"})["data"]
		self.assertEqual(data["_messages"], ["Saved with warning"])  # stale messages drained first

	def test_workflow_transition_that_submits_needs_submit_grant(self):
		denied = self.h.call("apply_workflow_action", doctype="Purchase Order", name="PO-1", action="Approve")
		self.assertTrue(denied["isError"])
		self.assertIn("Submit", denied["text"])
		ok = self.h.call("apply_workflow_action", doctype="Purchase Order", name="PO-1", action="Reject")
		self.assertFalse(ok["isError"], ok["text"])
		unknown = self.h.call("apply_workflow_action", doctype="Purchase Order", name="PO-1", action="Escalate")
		self.assertIn("Available", unknown["text"])

	def test_tool_outside_policy_is_unknown(self):
		response = self.h.rpc("tools/call", {"name": "delete_document", "arguments": {"doctype": "Sales Invoice", "name": "SINV-1"}})
		self.assertEqual(response["error"]["code"], INVALID_PARAMS)
		self.assertEqual(self.h.audit.entries[-1].status, DENIED)
		self.assertIn("SINV-1", self.h.gateway.docs["Sales Invoice"])

	def test_nonexistent_tool(self):
		self.assertEqual(self.h.rpc("tools/call", {"name": "rm_rf"})["error"]["code"], INVALID_PARAMS)


class TestReportsAndCustomActions(unittest.TestCase):
	def setUp(self):
		self.h = Harness()

	def test_list_and_describe_reports(self):
		data = self.h.call("list_reports")["data"]
		self.assertEqual([r["report_name"] for r in data["reports"]], ["Sales Register"])
		self.assertTrue(data["reports"][0]["has_fixed_filters"])
		described = self.h.call("describe_report", report_name="Sales Register")["data"]
		self.assertEqual(described["fixed_filters"], {"company": "ACME"})
		self.assertEqual(described["notes"], "Company fixed")

	def test_fixed_filters_are_enforced(self):
		data = self.h.call("run_report", report_name="Sales Register", filters={"company": "Globex"})["data"]
		self.assertEqual(data["filters_applied"], {"company": "ACME"})
		self.assertEqual(self.h.gateway.last_report_filters, {"company": "ACME"})
		self.assertEqual([r["name"] for r in data["rows"]], ["SINV-1"])

	def test_report_not_granted(self):
		self.assertTrue(self.h.call("run_report", report_name="Secret Report")["isError"])

	def test_report_filters_are_validated(self):
		result = self.h.call("run_report", report_name="Sales Register", filters={"company`": "x"})
		self.assertTrue(result["isError"])

	def test_custom_action(self):
		data = self.h.call("stock_balance", item_code="WIDGET")["data"]
		self.assertEqual(data["result"], {"item_code": "WIDGET", "qty": 42})
		self.assertTrue(self.h.call("stock_balance")["isError"])
		self.assertTrue(self.h.call("stock_balance", item_code="W", warehouse="X")["isError"])

	def test_blocked_custom_method(self):
		result = self.h.call("sneaky")
		self.assertTrue(result["isError"])
		self.assertIn("not allowed", result["text"])


class TestMultiProfileAndAudit(unittest.TestCase):
	def test_union_of_scopes_across_profiles(self):
		acme = ProfileSpec("ACME", users=frozenset({"u"}), action_types=frozenset({READ}), actions=frozenset({"list_documents", "count_documents"}),
			doctypes=(DoctypeRule("Sales Invoice", frozenset({READ}), scope=(("company", "=", "ACME"),)),))
		globex = ProfileSpec("Globex", users=frozenset({"u"}), action_types=frozenset({READ}), actions=frozenset({"list_documents"}),
			doctypes=(DoctypeRule("Sales Invoice", frozenset({READ}), scope=(("company", "=", "Globex"),)),))
		h = Harness(profiles=(acme, globex), user="u", roles=())
		data = h.call("list_documents", doctype="Sales Invoice", order_by="grand_total desc", limit=5)["data"]
		self.assertEqual([r["name"] for r in data["data"]], ["SINV-3", "SINV-2", "SINV-1"])
		# Only the ACME profile enables count_documents, so only its rows are counted.
		self.assertEqual(h.call("count_documents", doctype="Sales Invoice")["data"]["count"], 2)

	def test_auditor_sees_everything_read_only(self):
		h = Harness(user="auditor@x.com", roles=())
		self.assertEqual(h.call("count_documents", doctype="Sales Invoice")["data"]["count"], 3)
		self.assertNotIn("create_document", h.tools())

	def test_audit_entries_redact_secrets(self):
		h = Harness()
		h.call("create_document", doctype="Customer", data={"customer_name": "G", "portal_password": "hunter2"})
		entry = h.audit.entries[-1]
		self.assertEqual(entry.arguments["data"]["portal_password"], "hunter2")  # raw in memory …
		from mcp_connector.domain.audit import serialize_arguments

		self.assertIn(REDACTED, serialize_arguments(entry.arguments))  # … redacted when stored
		self.assertNotIn("hunter2", serialize_arguments(entry.arguments))
		self.assertEqual(entry.client_ip, "10.0.0.1")
		self.assertEqual(entry.profiles, ("Sales Agent",))

	def test_audit_modes(self):
		quiet = Harness(settings=ServerSettings(audit_mode=AUDIT_WRITES_AND_FAILURES))
		quiet.call("list_doctypes")
		self.assertEqual(quiet.audit.entries, [])
		quiet.call("update_document", doctype="Customer", name="CUST-1", data={"territory": "Y"})
		self.assertEqual(len(quiet.audit.entries), 1)
		off = Harness(settings=ServerSettings(audit_mode=AUDIT_DISABLED))
		off.call("update_document", doctype="Customer", name="CUST-1", data={"territory": "Y"})
		self.assertEqual(off.audit.entries, [])

	def test_arguments_not_logged_when_disabled(self):
		h = Harness(settings=ServerSettings(log_arguments=False))
		h.call("list_doctypes")
		self.assertIsNone(h.audit.entries[-1].arguments)


def _p(name, types, actions, doctypes=(), reports=()):
	return ProfileSpec(name, users=frozenset({"u"}), action_types=frozenset(types), actions=frozenset(actions), doctypes=tuple(doctypes), reports=tuple(reports))


class TestReviewRegressions(unittest.TestCase):
	"""Scenarios from the independent security review: profiles are alternatives, never blended."""

	def test_fields_of_one_profile_never_apply_to_rows_of_another(self):
		a = _p("A", {READ}, {"get_document", "list_documents"}, [DoctypeRule("Customer", frozenset({READ}), scope=(("territory", "=", "North"),))])
		b = _p("B", {READ}, {"get_document", "list_documents"}, [DoctypeRule("Customer", frozenset({READ}), FieldRule(ONLY_LISTED, frozenset({"customer_name"})))])
		h = Harness(profiles=(a, b), user="u", roles=())
		south = h.call("get_document", doctype="Customer", name="CUST-2")["data"]
		self.assertEqual(south.get("customer_name"), "Beta")
		self.assertNotIn("territory", south)
		self.assertNotIn("credit_limit", south)
		north = h.call("get_document", doctype="Customer", name="CUST-1")["data"]
		self.assertEqual(north["credit_limit"], 5000)
		rows = h.call("list_documents", doctype="Customer", fields=["name", "credit_limit"])["data"]["data"]
		self.assertEqual([r["name"] for r in rows], ["CUST-1"])  # credit_limit is only visible under A's scope
		self.assertEqual(len(h.call("list_documents", doctype="Customer", fields=["name", "customer_name"])["data"]["data"]), 2)

	def test_write_needs_one_profile_covering_fields_and_record(self):
		a = _p("A", {READ, UPDATE}, {"update_document"}, [DoctypeRule("Customer", frozenset({UPDATE}), FieldRule(ONLY_LISTED, frozenset({"territory"})))])
		b = _p("B", {READ, UPDATE}, {"update_document"}, [DoctypeRule("Customer", frozenset({UPDATE}), scope=(("territory", "=", "North"),))])
		h = Harness(profiles=(a, b), user="u", roles=())
		denied = h.call("update_document", doctype="Customer", name="CUST-2", data={"credit_limit": 1})
		self.assertTrue(denied["isError"])
		self.assertEqual(h.gateway.docs["Customer"]["CUST-2"]["credit_limit"], 100)
		self.assertFalse(h.call("update_document", doctype="Customer", name="CUST-2", data={"territory": "West"})["isError"])
		self.assertFalse(h.call("update_document", doctype="Customer", name="CUST-1", data={"credit_limit": 9})["isError"])

	def test_tool_from_one_profile_does_not_combine_with_grant_from_another(self):
		a = _p("A", {READ, DELETE}, {"delete_document", "list_documents"})
		b = _p("B", {READ, DELETE}, {"list_documents"}, [DoctypeRule("Customer", frozenset({READ, DELETE}))])
		h = Harness(profiles=(a, b), user="u", roles=())
		self.assertNotIn("delete_document", h.tools())
		response = h.rpc("tools/call", {"name": "delete_document", "arguments": {"doctype": "Customer", "name": "CUST-1"}})
		self.assertIn("error", response)
		self.assertIn("CUST-1", h.gateway.docs["Customer"])

	def test_workflow_submit_needs_submit_grant_in_scope(self):
		upd = _p("Upd", {READ, UPDATE}, {"apply_workflow_action"}, [DoctypeRule("Purchase Order", frozenset({UPDATE}))])
		sub = _p("Sub", {READ, SUBMIT}, {"apply_workflow_action"}, [DoctypeRule("Purchase Order", frozenset({SUBMIT}), scope=(("supplier", "=", "S2"),))])
		h = Harness(profiles=(upd, sub), user="u", roles=())
		result = h.call("apply_workflow_action", doctype="Purchase Order", name="PO-1", action="Approve")
		self.assertTrue(result["isError"])
		self.assertEqual(h.gateway.docs["Purchase Order"]["PO-1"]["docstatus"], 0)

	def test_workflow_submit_grant_must_come_from_the_updating_profile(self):
		# Update from one profile, Submit from another: neither alone allows submitting.
		upd = _p("Upd", {READ, UPDATE}, {"apply_workflow_action"}, [DoctypeRule("Purchase Order", frozenset({UPDATE}))])
		sub = _p("Sub", {READ, SUBMIT}, {"apply_workflow_action"}, [DoctypeRule("Purchase Order", frozenset({SUBMIT}))])
		h = Harness(profiles=(upd, sub), user="u", roles=())
		self.assertTrue(h.call("apply_workflow_action", doctype="Purchase Order", name="PO-1", action="Approve")["isError"])
		both = _p("Both", {READ, UPDATE, SUBMIT}, {"apply_workflow_action"}, [DoctypeRule("Purchase Order", frozenset({UPDATE, SUBMIT}))])
		h = Harness(profiles=(both,), user="u", roles=())
		self.assertFalse(h.call("apply_workflow_action", doctype="Purchase Order", name="PO-1", action="Approve")["isError"])

	def test_write_response_needs_a_profile_that_reads_records(self):
		# The only Read grant comes from a profile without get_document: nothing is echoed back.
		read = _p("R", {READ}, {"list_doctypes"}, [DoctypeRule("Customer", frozenset({READ}))])
		upd = _p("U", {UPDATE}, {"update_document"}, [DoctypeRule("Customer", frozenset({UPDATE}))])
		h = Harness(profiles=(read, upd), user="u", roles=())
		data = h.call("update_document", doctype="Customer", name="CUST-1", data={"territory": "East"})["data"]
		self.assertEqual(set(data), {"doctype", "name", "docstatus"})

	def test_list_doctypes_only_lists_operations_a_tool_can_perform(self):
		p = _p("P", {READ, DELETE}, {"list_doctypes", "list_documents"}, [DoctypeRule("Customer", frozenset({READ, DELETE}))])
		rows = Harness(profiles=(p,), user="u", roles=()).call("list_doctypes")["data"]["doctypes"]
		self.assertEqual(rows[0]["operations"], [READ])  # Delete is granted but delete_document is not enabled

	def test_write_response_respects_read_scope(self):
		read = _p("R", {READ}, {"get_document"}, [DoctypeRule("Sales Invoice", frozenset({READ}), scope=(("company", "=", "ACME"),))])
		upd = _p("U", {UPDATE}, {"update_document"}, [DoctypeRule("Sales Invoice", frozenset({UPDATE}))])
		h = Harness(profiles=(read, upd), user="u", roles=())
		data = h.call("update_document", doctype="Sales Invoice", name="SINV-2", data={"customer": "CUST-1"})["data"]
		self.assertEqual(set(data), {"doctype", "name", "docstatus"})
		acme = h.call("update_document", doctype="Sales Invoice", name="SINV-1", data={"customer": "CUST-2"})["data"]
		self.assertEqual(acme["customer"], "CUST-2")

	def test_workflow_doctypes_cannot_be_submitted_or_state_set_directly(self):
		p = _p(
			"W",
			{READ, UPDATE, SUBMIT},
			{"submit_document", "update_document"},
			[DoctypeRule("Purchase Order", frozenset({READ, UPDATE, SUBMIT}))],
		)
		h = Harness(profiles=(p,), user="u", roles=())
		self.assertIn("apply_workflow_action", h.call("submit_document", doctype="Purchase Order", name="PO-1")["text"])
		state = h.call("update_document", doctype="Purchase Order", name="PO-1", data={"workflow_state": "Approved"})
		self.assertTrue(state["isError"])
		self.assertEqual(h.gateway.docs["Purchase Order"]["PO-1"]["workflow_state"], "Pending")

	def test_deep_paging_across_scopes_is_bounded(self):
		a = _p("A", {READ}, {"list_documents"}, [DoctypeRule("Sales Invoice", frozenset({READ}), scope=(("company", "=", "ACME"),))])
		b = _p("B", {READ}, {"list_documents"}, [DoctypeRule("Sales Invoice", frozenset({READ}), scope=(("company", "=", "Globex"),))])
		h = Harness(profiles=(a, b), user="u", roles=())
		self.assertTrue(h.call("list_documents", doctype="Sales Invoice", start=9_999_999)["isError"])

	def test_report_builder_filters_limited_to_report_columns(self):
		p = _p("R", {REPORT}, {"run_report"}, reports=[ReportRule("Invoice Builder")])
		h = Harness(profiles=(p,), user="u", roles=())
		self.assertTrue(h.call("run_report", report_name="Invoice Builder", filters={"grand_total": [">", 150]})["isError"])
		self.assertFalse(h.call("run_report", report_name="Invoice Builder", filters={"customer": "CUST-1"})["isError"])


class TestCustomFields(unittest.TestCase):
	"""Adding a Custom Field alters a table and commits at once, so every refusal must come before the write."""

	FIELD = {"dt": "Customer", "label": "Region", "fieldtype": "Data", "insert_after": "customer_name"}

	def harness(self, scope=()):
		p = _p("F", {READ, CREATE, UPDATE}, {"create_document", "update_document"}, [DoctypeRule("Custom Field", frozenset({READ, CREATE, UPDATE}), scope=scope)])
		h = Harness(profiles=(p,), user="u", roles=())
		h.gateway.add_doctype(
			"Custom Field",
			[field(f) for f in ("dt", "label", "fieldname", "insert_after", "options", "fetch_from", "default")]
			+ [field("fieldtype", "Select"), field("depends_on", "Code"), field("ignore_xss_filter", "Check"), field("is_virtual", "Check")],
		)
		h.gateway.add_doc("Custom Field", "Customer-legacy_html", dt="Customer", fieldtype="HTML", options="<b>hi</b>")
		return h

	def refuse_writes(self, h):
		def fail(*args):
			raise AssertionError("wrote before every check passed")

		h.gateway.create_document = h.gateway.update_document = fail

	def test_add_custom_field(self):
		h = self.harness()
		result = h.call("create_document", doctype="Custom Field", data=self.FIELD)
		self.assertFalse(result["isError"], result["text"])
		self.assertEqual(h.gateway.docs["Custom Field"][result["data"]["name"]]["label"], "Region")
		self.assertIn("create_document", h.tools())

	def test_unsafe_settings_are_refused_before_writing(self):
		h = self.harness()
		self.refuse_writes(h)
		for extra in ({"dt": "User"}, {"fieldtype": "HTML", "options": "<script>x</script>"}, {"depends_on": "eval:1"}, {"ignore_xss_filter": 1}):
			result = h.call("create_document", doctype="Custom Field", data={**self.FIELD, **extra})
			self.assertTrue(result["isError"], extra)
			self.assertEqual(h.audit.entries[-1].status, DENIED)

	def test_update_is_checked_against_the_saved_field(self):
		h = self.harness()
		self.refuse_writes(h)
		result = h.call("update_document", doctype="Custom Field", name="Customer-legacy_html", data={"options": "<img src=x onerror=alert(1)>"})
		self.assertTrue(result["isError"])
		self.assertIn("HTML fields", result["text"])

	def test_scoped_grant_is_refused_before_writing(self):
		h = self.harness(scope=(("dt", "=", "Customer"),))
		self.refuse_writes(h)
		result = h.call("create_document", doctype="Custom Field", data=self.FIELD)
		self.assertTrue(result["isError"])
		self.assertIn("record scope", result["text"])


class TestNamingSeries(unittest.TestCase):
	def harness(self, rule=None):
		rule = rule or DoctypeRule("Customer", frozenset({READ, UPDATE}))
		p = _p("N", {READ, UPDATE}, {"set_naming_series", "list_doctypes"}, [rule, DoctypeRule("Sales Invoice", frozenset({READ, UPDATE}))])
		h = Harness(profiles=(p,), user="u", roles=())
		h.gateway.add_doctype("Customer", [field("customer_name"), field("naming_series", "Select", options="CUST-.YYYY.-")])
		h.gateway.add_doctype(NAMING_SETTINGS, [], issingle=True)
		return h

	@staticmethod
	def series_calls(h):
		return [c for c in h.gateway.calls if c[0] == "naming_series"]

	def test_new_default_keeps_existing_series(self):
		h = self.harness()
		result = h.call("set_naming_series", doctype="Customer", series="C-.#####")
		self.assertFalse(result["isError"], result["text"])
		self.assertEqual(self.series_calls(h), [("naming_series", "Customer", ["C-.#####", "CUST-.YYYY.-"], False)])
		self.assertIn("Selling Settings", result["data"]["note"])
		self.assertEqual(h.audit.entries[-1].status, SUCCESS)

	def test_refusals_never_reach_frappe(self):
		scoped = DoctypeRule("Customer", frozenset({UPDATE}), scope=(("territory", "=", "North"),))
		hidden = DoctypeRule("Customer", frozenset({UPDATE}), FieldRule(EXCLUDE_LISTED, frozenset({"naming_series"})))
		cases = [
			(None, "Customer", "C-<b>.###", "series must"),
			(None, "Sales Invoice", "SI-.###", "not named by a naming series"),
			(scoped, "Customer", "C-.#####", "with no scope"),
			(hidden, "Customer", "C-.#####", "does not hide"),
		]
		for rule, doctype, series, message in cases:
			h = self.harness(rule)
			result = h.call("set_naming_series", doctype=doctype, series=series)
			self.assertTrue(result["isError"], message)
			self.assertIn(message, result["text"])
			self.assertEqual(self.series_calls(h), [])

	def test_needs_erp_permission_on_naming_settings(self):
		h = self.harness()
		h.gateway.denied.add((NAMING_SETTINGS, "write"))
		self.assertIn("naming series", h.call("set_naming_series", doctype="Customer", series="C-.#####")["text"])
		self.assertEqual(self.series_calls(h), [])

	def test_it_does_not_count_as_updating_records(self):
		h = self.harness()
		self.assertNotIn("set_naming_series", OPERATION_TOOLS[UPDATE])
		self.assertEqual(h.call("list_doctypes")["data"]["count"], 0)  # no record tool enabled


if __name__ == "__main__":
	unittest.main()
