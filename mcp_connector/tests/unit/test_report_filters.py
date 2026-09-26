import unittest

from mcp_connector.domain.report_filters import parse_report_filters

GENERAL_LEDGER = """
// Copyright (c) 2018
frappe.query_reports["General Ledger"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
			reqd: 1,
			width: "60px",
		},
		{
			fieldname: "account",
			label: __("Account"),
			fieldtype: "MultiSelectList",
			options: "Account",
			get_data: function (txt) {
				return frappe.db.get_link_options("Account", txt, { company: "x" });
			},
		},
		{ fieldtype: "Break" },
		{
			fieldname: "group_by",
			label: __("Group by"),
			fieldtype: "Select",
			options: ["", __("Group by Voucher"), "Group by Account"],
			default: "Group by Voucher",
		},
		{
			"fieldname": "include_dimensions",
			"label": __("Consider Accounting Dimensions"),
			"fieldtype": "Check",
			"default": 1, // trailing comment with } and ]
		},
		{
			fieldname: "note",
			label: "Say \\"hi\\": {not a brace}",
			fieldtype: "Data",
			reqd: 0,
		},
	],
	formatter: function (value) { return value; },
};
"""


class TestReportFilterParser(unittest.TestCase):
	def setUp(self):
		self.filters = parse_report_filters(GENERAL_LEDGER)

	def test_finds_every_field_and_skips_breaks(self):
		self.assertEqual(
			[f["fieldname"] for f in self.filters],
			["company", "from_date", "account", "group_by", "include_dimensions", "note"],
		)

	def test_literal_values(self):
		company = self.filters[0]
		self.assertEqual(company, {"fieldname": "company", "label": "Company", "fieldtype": "Link", "options": "Company", "reqd": True})

	def test_computed_defaults_are_dropped(self):
		self.assertNotIn("default", self.filters[1])

	def test_array_options_and_quoted_keys(self):
		self.assertEqual(self.filters[3]["options"], "\nGroup by Voucher\nGroup by Account")
		self.assertEqual(self.filters[3]["default"], "Group by Voucher")
		self.assertEqual(self.filters[4]["default"], 1)
		self.assertEqual(self.filters[4]["label"], "Consider Accounting Dimensions")

	def test_strings_with_braces_and_escapes(self):
		self.assertEqual(self.filters[5]["label"], 'Say "hi": {not a brace}')
		self.assertNotIn("reqd", self.filters[5])

	def test_dynamic_or_missing_filters(self):
		self.assertIsNone(parse_report_filters('frappe.query_reports["X"] = $.extend({}, erpnext.financial_statements);'))
		self.assertIsNone(parse_report_filters(""))
		self.assertIsNone(parse_report_filters("filters: [ {fieldname: 'x'"))


if __name__ == "__main__":
	unittest.main()
