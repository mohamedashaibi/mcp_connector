import json

import frappe

from mcp_connector.domain.catalog import BUILTIN_TOOLS
from mcp_connector.infrastructure.install import sync_standard_actions
from mcp_connector.infrastructure.policy_source import load_actions, load_profiles
from mcp_connector.infrastructure.settings import load_settings
from mcp_connector.tests import fixtures
from mcp_connector.tests.integration.base import McpTestCase


class TestInstallation(McpTestCase):
	def test_standard_actions_are_synced(self):
		standard = set(frappe.get_all("MCP Action", filters={"is_standard": 1}, pluck="name"))
		self.assertEqual(standard, {t.name for t in BUILTIN_TOOLS})

	def test_manager_role_page_and_workspace_exist(self):
		self.assertTrue(frappe.db.exists("Role", "MCP Manager"))
		self.assertTrue(frappe.db.exists("Page", "mcp-console"))
		self.assertTrue(frappe.db.exists("Workspace", "MCP Connector"))

	def test_settings_defaults(self):
		settings = load_settings()
		self.assertTrue(settings.default_page_size <= settings.max_page_size)
		self.assertIn(settings.audit_mode, ("All Requests", "Writes and Failures", "Disabled"))

	def test_sync_keeps_admin_choices_and_restores_code_owned_fields(self):
		doc = frappe.get_doc("MCP Action", "count_documents")
		doc.enabled = 0
		doc.description = "Custom wording"
		doc.save()
		frappe.db.set_value("MCP Action", "count_documents", "title", "Tampered")
		sync_standard_actions()
		doc.reload()
		self.assertEqual((doc.enabled, doc.description, doc.title), (0, "Custom wording", "Count documents"))

	def test_policy_cache_refreshes_after_profile_save(self):
		load_profiles()
		self.make_profile("Cache Probe", users=["Administrator"])
		self.assertIn("Cache Probe", [p.name for p in load_profiles()])
		frappe.delete_doc("MCP Profile", "Cache Probe", ignore_permissions=True)
		self.assertNotIn("Cache Probe", [p.name for p in load_profiles()])


class TestProfileValidation(McpTestCase):
	def assertRejected(self, **kwargs):
		with self.assertRaises(frappe.ValidationError):
			self.make_profile("Invalid Profile", users=["Administrator"], **kwargs)

	def test_never_exposed_doctype(self):
		self.assertRejected(doctypes=[{"doctype": "MCP Profile"}])

	def test_child_table_doctype(self):
		self.assertRejected(doctypes=[{"doctype": fixtures.ORDER_ITEM}])

	def test_write_on_read_only_doctype(self):
		self.assertRejected(doctypes=[{"doctype": "User", "ops": ("Read", "Update")}])
		self.make_profile("Users Read Only", users=["Administrator"], doctypes=[{"doctype": "User"}])

	def test_submit_on_non_submittable(self):
		self.assertRejected(doctypes=[{"doctype": fixtures.REQUEST, "ops": ("Read", "Submit")}])

	def test_operation_required(self):
		self.assertRejected(doctypes=[{"doctype": fixtures.ORDER, "ops": ()}])

	def test_unknown_field_in_field_list(self):
		self.assertRejected(doctypes=[{"doctype": fixtures.ORDER, "field_mode": "Only Listed Fields", "field_list": "customer\nnope"}])

	def test_only_listed_needs_fields(self):
		self.assertRejected(doctypes=[{"doctype": fixtures.ORDER, "field_mode": "Only Listed Fields", "field_list": ""}])

	def test_scope_must_be_valid(self):
		self.assertRejected(doctypes=[{"doctype": fixtures.ORDER, "scope": "{not json"}])
		self.assertRejected(doctypes=[{"doctype": fixtures.ORDER, "scope": '{"ghost": 1}'}])
		self.assertRejected(doctypes=[{"doctype": fixtures.ORDER, "scope": '{"items": "x"}'}])
		self.assertRejected(doctypes=[{"doctype": fixtures.ORDER, "scope": '[["company", "regexp", "x"]]'}])

	def test_fixed_filters_must_be_object(self):
		self.assertRejected(reports=[{"report": fixtures.REPORT, "fixed": "[1, 2]"}])

	def test_duplicates_rejected_or_removed(self):
		self.assertRejected(doctypes=[{"doctype": fixtures.ORDER}, {"doctype": fixtures.ORDER}])
		doc = self.make_profile("Dedupe", roles=[fixtures.SALES_ROLE, fixtures.SALES_ROLE])
		self.assertEqual([r.role for r in doc.roles], [fixtures.SALES_ROLE])

	def test_valid_profile_is_normalized(self):
		doc = self.make_profile(
			"Normalized",
			users=["Administrator"],
			doctypes=[
				{
					"doctype": fixtures.ORDER,
					"field_mode": "Exclude Listed Fields",
					"field_list": "secret_note, amount",
					"scope": '{"company":"ACME"}',
				}
			],
		)
		row = doc.doctypes[0]
		self.assertEqual(row.field_list, "amount\nsecret_note")
		self.assertEqual(json.loads(row.scope_filters), {"company": "ACME"})


class TestActionValidation(McpTestCase):
	def custom(self, **values):
		return frappe.get_doc(
			{
				"doctype": "MCP Action",
				"tool_name": values.pop("tool_name", "test_ping"),
				"action_type": "Custom",
				"description": "Test action",
				"method": "frappe.ping",
				**values,
			}
		)

	def test_valid_custom_action(self):
		doc = self.custom().insert()
		self.assertEqual(json.loads(doc.input_schema)["type"], "object")

	def test_tool_name_format(self):
		for bad in ("Bad Name", "1starts_with_digit", "has-dash", "x"):
			with self.assertRaises(frappe.ValidationError, msg=bad):
				self.custom(tool_name=bad).insert()

	def test_blocked_and_unsafe_methods(self):
		for method in ("frappe.client.get_list", "frappe.utils.now", "no.such.module.fn", "mcp_connector.api.mcp.endpoint"):
			with self.assertRaises(frappe.ValidationError, msg=method):
				self.custom(tool_name="bad_method", method=method).insert()

	def test_input_schema_validated(self):
		with self.assertRaises(frappe.ValidationError):
			self.custom(input_schema='{"type": "array"}').insert()
		with self.assertRaises(frappe.ValidationError):
			self.custom(input_schema="{broken").insert()

	def test_standard_actions_are_protected(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("MCP Action", "list_documents")
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "MCP Action", "tool_name": "fake_standard", "action_type": "Read", "is_standard": 1}).insert()
		doc = frappe.get_doc("MCP Action", "list_documents")
		doc.action_type = "Delete"
		doc.save()
		self.assertEqual(doc.action_type, "Read")

	def test_deleting_custom_action_detaches_it_from_profiles(self):
		self.custom(tool_name="detach_me").insert()
		self.make_profile("Uses Custom", users=["Administrator"], types=("Custom",), actions=["detach_me"])
		frappe.delete_doc("MCP Action", "detach_me")
		self.assertEqual([a.action for a in frappe.get_doc("MCP Profile", "Uses Custom").actions], [])
		self.assertNotIn("detach_me", [a.name for a in load_actions()])
