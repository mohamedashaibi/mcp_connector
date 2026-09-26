import unittest

from mcp_connector.application.console import ListDoctypeCatalog, ListReportCatalog
from mcp_connector.application.console_dto import parse_action, parse_log_query, parse_profile, parse_settings
from mcp_connector.domain.errors import InvalidArguments

SETTINGS = {
	"enabled": True,
	"server_name": "ERPNext MCP",
	"instructions": "",
	"default_page_size": 20,
	"max_page_size": 100,
	"max_report_rows": 200,
	"rate_limit_per_minute": 120,
	"audit_mode": "All Requests",
	"log_arguments": True,
	"log_retention_days": 30,
	"allowed_origins": [],
}


class TestConsolePayloads(unittest.TestCase):
	def test_settings_accept_empty_optional_text(self):
		# Regression: an empty "instructions" used to be reported as missing.
		self.assertEqual(parse_settings(SETTINGS).instructions, "")

	def test_settings_reject_missing_unknown_and_out_of_range(self):
		for bad in (
			{k: v for k, v in SETTINGS.items() if k != "audit_mode"},
			{**SETTINGS, "debug": True},
			{**SETTINGS, "max_page_size": 0},
			{**SETTINGS, "rate_limit_per_minute": "10"},
			{**SETTINGS, "audit_mode": "Verbose"},
			{**SETTINGS, "server_name": ""},
		):
			with self.assertRaises(InvalidArguments):
				parse_settings(bad)

	def test_profile_minimal_and_full(self):
		minimal = parse_profile({"profile_name": "  Sales  "})
		self.assertEqual((minimal.profile_name, minimal.enabled, minimal.doctypes), ("Sales", True, ()))
		full = parse_profile(
			{
				"profile_name": "Ops",
				"roles": ["Sales User", "Sales User"],
				"action_types": ["Read", "Create"],
				"doctypes": [{"doctype": "Customer", "operations": ["Read"], "field_mode": "Only Listed Fields", "field_list": ["customer_name"], "scope": {"territory": "North"}}],
				"reports": [{"report": "General Ledger", "fixed_filters": {"company": "ACME"}}],
			}
		)
		self.assertEqual(full.roles, ("Sales User",))
		self.assertEqual(full.doctypes[0].scope, {"territory": "North"})
		self.assertEqual(full.reports[0].fixed_filters, {"company": "ACME"})

	def test_profile_rejects_bad_shapes(self):
		for bad in (
			{},
			{"profile_name": ""},
			{"profile_name": "x", "extra": 1},
			{"profile_name": "x", "action_types": ["Admin"]},
			{"profile_name": "x", "doctypes": [{"doctype": "Customer", "operations": ["Read"], "field_mode": "Some"}]},
			{"profile_name": "x", "doctypes": [{"doctype": "Customer", "scope": "company=ACME"}]},
			{"profile_name": "x", "reports": [{"report": "GL", "fixed_filters": ["company"]}]},
			{"profile_name": "x", "enabled": "true"},
			"not an object",
		):
			with self.assertRaises(InvalidArguments, msg=str(bad)):
				parse_profile(bad)

	def test_action_and_log_query(self):
		action = parse_action({"tool_name": "stock_balance", "method": "erp.api.stock", "input_schema": {"type": "object"}})
		self.assertEqual((action.action_type, action.is_new), ("Custom", False))
		with self.assertRaises(InvalidArguments):
			parse_action({"tool_name": "x", "action_type": "Root"})
		self.assertEqual(parse_log_query(None).limit, 50)
		with self.assertRaises(InvalidArguments):
			parse_log_query({"limit": 10_000})


class FakeCatalog:
	def doctypes(self):
		return [
			{"name": n, "module": m, "issingle": False, "is_submittable": False, "custom": False}
			for n, m in (("Customer", "Selling"), ("User", "Core"), ("MCP Profile", "MCP Connector"), ("System Console", "Desk"))
		]

	def reports(self):
		return [{"name": "General Ledger", "module": "Accounts", "report_type": "Script Report", "ref_doctype": "GL Entry"}]


class TestCatalogs(unittest.TestCase):
	def test_doctype_catalog_hides_protected_and_flags_read_only(self):
		rows = {r["name"]: r for r in ListDoctypeCatalog(FakeCatalog()).execute()}
		self.assertEqual(set(rows), {"Customer", "User"})
		self.assertTrue(rows["Customer"]["writable"])
		self.assertFalse(rows["User"]["writable"])

	def test_report_catalog(self):
		self.assertEqual([r["name"] for r in ListReportCatalog(FakeCatalog()).execute()], ["General Ledger"])


if __name__ == "__main__":
	unittest.main()
