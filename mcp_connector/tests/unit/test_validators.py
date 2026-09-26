import unittest

from mcp_connector.domain.audit import (
	AUDIT_ALL,
	AUDIT_DISABLED,
	AUDIT_WRITES_AND_FAILURES,
	DENIED,
	REDACTED,
	SUCCESS,
	redact,
	serialize_arguments,
	should_record,
)
from mcp_connector.domain.errors import InvalidArguments
from mcp_connector.domain.filters import (
	clamp_int,
	normalize_filters,
	order_by_sql,
	parse_fields,
	parse_order_by,
	require_fields,
	sort_rows,
)
from mcp_connector.domain.jsonrpc import INVALID_PARAMS, INVALID_REQUEST, RpcError, parse_message
from mcp_connector.domain.protected import is_blocked_method, is_exposable, is_writable
from mcp_connector.domain.schema import validate_arguments, validate_input_schema

ALLOWED = {"status", "grand_total", "customer", "posting_date", "name", "modified"}.__contains__


class TestFilters(unittest.TestCase):
	def test_dict_forms(self):
		self.assertEqual(
			normalize_filters({"status": "Open", "grand_total": [">", 100], "customer": ["A", "B"]}),
			(("status", "=", "Open"), ("grand_total", ">", 100), ("customer", "in", ["A", "B"])),
		)

	def test_list_forms_including_frappe_four_item_rows(self):
		self.assertEqual(
			normalize_filters([["status", "=", "Open"], ["Sales Invoice", "posting_date", "between", ["2024-01-01", "2024-01-31"]]]),
			(("status", "=", "Open"), ("posting_date", "between", ["2024-01-01", "2024-01-31"])),
		)

	def test_empty_values(self):
		for raw in (None, "", {}, []):
			self.assertEqual(normalize_filters(raw), ())

	def test_rejects_sql_in_fieldname(self):
		with self.assertRaises(InvalidArguments):
			normalize_filters({"name`; drop table x; --": "1"})

	def test_rejects_unknown_operator(self):
		with self.assertRaises(InvalidArguments):
			normalize_filters([["status", "regexp", "x"]])

	def test_operator_case_is_normalized(self):
		self.assertEqual(normalize_filters([["status", "LIKE", "%x%"]]), (("status", "like", "%x%"),))

	def test_in_accepts_comma_string(self):
		self.assertEqual(normalize_filters([["status", "in", "Open, Closed"]]), (("status", "in", ["Open", "Closed"]),))

	def test_between_needs_two_values(self):
		with self.assertRaises(InvalidArguments):
			normalize_filters([["posting_date", "between", ["2024-01-01"]]])

	def test_is_only_set_or_not_set(self):
		self.assertEqual(normalize_filters([["customer", "is", "set"]]), (("customer", "is", "set"),))
		with self.assertRaises(InvalidArguments):
			normalize_filters([["customer", "is", "null"]])

	def test_rejects_object_values(self):
		with self.assertRaises(InvalidArguments):
			normalize_filters({"status": {"$ne": 1}})

	def test_require_fields_blocks_hidden_field_probing(self):
		with self.assertRaises(InvalidArguments):
			require_fields((("salary", ">", 1000),), ALLOWED)
		require_fields((("status", "=", "Open"),), ALLOWED)

	def test_parse_fields(self):
		self.assertEqual(parse_fields(["name", "status", "status"], ALLOWED), ["name", "status"])
		self.assertEqual(parse_fields(None, ALLOWED), [])
		self.assertEqual(parse_fields("name, status", ALLOWED), ["name", "status"])
		with self.assertRaises(InvalidArguments):
			parse_fields(["salary"], ALLOWED)
		with self.assertRaises(InvalidArguments):
			parse_fields(["count(*)"], ALLOWED)

	def test_parse_order_by(self):
		self.assertEqual(parse_order_by(None, ALLOWED), (("modified", "desc"),))
		self.assertEqual(parse_order_by("grand_total DESC, name", ALLOWED), (("grand_total", "desc"), ("name", "asc")))
		self.assertEqual(order_by_sql((("grand_total", "desc"),)), "`grand_total` desc, `name` asc")
		self.assertEqual(order_by_sql((("name", "desc"),)), "`name` desc")
		for bad in ("(select 1)", "name; drop", "salary desc", "a,b,c,d", 5):
			with self.assertRaises(InvalidArguments):
				parse_order_by(bad, ALLOWED)

	def test_sort_rows_matches_database_order(self):
		rows = [{"name": "b", "v": 2}, {"name": "a", "v": None}, {"name": "c", "v": 10}]
		self.assertEqual([r["name"] for r in sort_rows(rows, (("v", "asc"),))], ["a", "b", "c"])
		self.assertEqual([r["name"] for r in sort_rows(rows, (("v", "desc"),))], ["c", "b", "a"])
		mixed_case = [{"name": "2", "t": "beta"}, {"name": "1", "t": "Alpha"}, {"name": "3", "t": "alpha"}]
		self.assertEqual([r["name"] for r in sort_rows(mixed_case, (("t", "asc"),))], ["1", "3", "2"])  # case-insensitive, name breaks ties

	def test_clamp_int(self):
		self.assertEqual(clamp_int(None, "limit", 20, 1, 100), 20)
		self.assertEqual(clamp_int(500, "limit", 20, 1, 100), 100)
		self.assertEqual(clamp_int("5", "limit", 20, 1, 100), 5)
		self.assertEqual(clamp_int(0, "limit", 20, 1, 100), 1)
		for bad in (True, "abc", 2.5):
			with self.assertRaises(InvalidArguments):
				clamp_int(bad, "limit", 20, 1, 100)


class TestSchema(unittest.TestCase):
	SCHEMA = {
		"type": "object",
		"properties": {
			"doctype": {"type": "string", "enum": ["ToDo", "Note"]},
			"limit": {"type": "integer", "minimum": 1, "maximum": 50},
			"fields": {"type": "array", "items": {"type": "string"}},
			"filters": {"type": ["object", "array"]},
		},
		"required": ["doctype"],
	}

	def test_valid_arguments_pass(self):
		self.assertEqual(
			validate_arguments(self.SCHEMA, {"doctype": "ToDo", "limit": 5, "fields": ["name"]}),
			{"doctype": "ToDo", "limit": 5, "fields": ["name"]},
		)

	def test_rejects_unknown_arguments(self):
		with self.assertRaises(InvalidArguments):
			validate_arguments(self.SCHEMA, {"doctype": "ToDo", "ignore_permissions": True})

	def test_rejects_missing_required(self):
		with self.assertRaises(InvalidArguments):
			validate_arguments(self.SCHEMA, {})

	def test_enum_is_enforced(self):
		with self.assertRaises(InvalidArguments):
			validate_arguments(self.SCHEMA, {"doctype": "User"})

	def test_types_and_bounds(self):
		for bad in ({"doctype": "ToDo", "limit": 0}, {"doctype": "ToDo", "limit": "5"}, {"doctype": "ToDo", "limit": True}, {"doctype": "ToDo", "fields": [1]}):
			with self.assertRaises(InvalidArguments):
				validate_arguments(self.SCHEMA, bad)

	def test_integral_float_is_accepted_as_integer(self):
		self.assertEqual(validate_arguments(self.SCHEMA, {"doctype": "ToDo", "limit": 5.0})["limit"], 5)

	def test_union_types(self):
		validate_arguments(self.SCHEMA, {"doctype": "ToDo", "filters": [["a", "=", 1]]})
		validate_arguments(self.SCHEMA, {"doctype": "ToDo", "filters": {"a": 1}})
		with self.assertRaises(InvalidArguments):
			validate_arguments(self.SCHEMA, {"doctype": "ToDo", "filters": "a=1"})

	def test_non_object_arguments(self):
		with self.assertRaises(InvalidArguments):
			validate_arguments(self.SCHEMA, ["ToDo"])
		self.assertEqual(validate_arguments({"type": "object", "properties": {}}, None), {})

	def test_input_schema_validation(self):
		schema = validate_input_schema({"type": "object", "properties": {"item_code": {"type": "string"}}, "required": ["item_code"]})
		self.assertEqual(schema["required"], ["item_code"])
		bad_schemas = [
			[],
			{"type": "array"},
			{"properties": {"bad name": {"type": "string"}}},
			{"properties": {"x": {"type": "date"}}},
			{"properties": {"x": {}}},
			{"properties": {"x": {"type": "string"}}, "required": ["y"]},
			{"properties": {}, "$ref": "#/defs"},
			{"properties": {"x": {"type": "string", "pattern": ".*"}}},
		]
		for bad in bad_schemas:
			with self.assertRaises(InvalidArguments, msg=str(bad)):
				validate_input_schema(bad)


class TestJsonRpc(unittest.TestCase):
	def test_request(self):
		req = parse_message({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
		self.assertEqual((req.id, req.method, req.params, req.is_notification), (7, "tools/list", {}, False))

	def test_notification(self):
		self.assertTrue(parse_message({"jsonrpc": "2.0", "method": "notifications/initialized"}).is_notification)

	def test_client_response_is_ignored(self):
		self.assertTrue(parse_message({"jsonrpc": "2.0", "id": 1, "result": {}}).is_notification)

	def test_errors(self):
		cases = [
			([{"jsonrpc": "2.0"}], INVALID_REQUEST),
			({"jsonrpc": "1.0", "id": 1, "method": "x"}, INVALID_REQUEST),
			({"jsonrpc": "2.0", "id": {"a": 1}, "method": "x"}, INVALID_REQUEST),
			({"jsonrpc": "2.0", "id": True, "method": "x"}, INVALID_REQUEST),
			({"jsonrpc": "2.0", "id": 1}, INVALID_REQUEST),
			({"jsonrpc": "2.0", "id": 1, "method": "x", "params": [1]}, INVALID_PARAMS),
		]
		for message, code in cases:
			with self.assertRaises(RpcError, msg=str(message)) as ctx:
				parse_message(message)
			self.assertEqual(ctx.exception.code, code)


class TestProtected(unittest.TestCase):
	def test_config_doctypes_are_never_exposed(self):
		for doctype in ("MCP Profile", "MCP Settings", "MCP Audit Log", "OAuth Bearer Token", "System Console"):
			self.assertFalse(is_exposable(doctype), doctype)

	def test_code_and_permission_doctypes_are_read_only(self):
		for doctype in ("Server Script", "User", "Role", "DocType", "Custom Field", "Report", "Print Format"):
			self.assertTrue(is_exposable(doctype))
			self.assertFalse(is_writable(doctype), doctype)
		self.assertTrue(is_writable("Sales Invoice"))

	def test_blocked_methods(self):
		for method in ("frappe.client.get_list", "frappe.client", "frappe.desk.form.save.savedocs", "mcp_connector.api.mcp.endpoint"):
			self.assertTrue(is_blocked_method(method), method)
		for method in ("erpnext.stock.utils.get_stock_balance", "frappe.clientele.x"):
			self.assertFalse(is_blocked_method(method), method)


class TestAuditRules(unittest.TestCase):
	def test_modes(self):
		self.assertTrue(should_record(AUDIT_ALL, "Read", SUCCESS))
		self.assertFalse(should_record(AUDIT_DISABLED, "Delete", DENIED))
		self.assertFalse(should_record(AUDIT_WRITES_AND_FAILURES, "Read", SUCCESS))
		self.assertTrue(should_record(AUDIT_WRITES_AND_FAILURES, "Read", DENIED))
		self.assertTrue(should_record(AUDIT_WRITES_AND_FAILURES, "Create", SUCCESS))

	def test_secrets_are_redacted_but_lookalikes_are_not(self):
		cleaned = redact({"data": {"new_password": "x", "api_secret": "y", "shipping_address": "z", "pin": "1234"}})
		self.assertEqual(cleaned["data"]["new_password"], REDACTED)
		self.assertEqual(cleaned["data"]["api_secret"], REDACTED)
		self.assertEqual(cleaned["data"]["pin"], REDACTED)
		self.assertEqual(cleaned["data"]["shipping_address"], "z")

	def test_serialized_arguments_are_bounded(self):
		self.assertLessEqual(len(serialize_arguments({"text": "x" * 10000}, max_chars=500)), 500)


if __name__ == "__main__":
	unittest.main()
