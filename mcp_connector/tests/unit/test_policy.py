import unittest

from mcp_connector.domain.action_types import CANCEL, CREATE, CUSTOM, DELETE, READ, REPORT, SUBMIT, UPDATE
from mcp_connector.domain.policy import (
	ALL_FIELDS,
	EXCLUDE_LISTED,
	ONLY_LISTED,
	ActionSpec,
	DoctypeRule,
	FieldRule,
	ProfileSpec,
	ReportRule,
	match_reason,
	parse_field_list,
	resolve_policy,
)

ACTIONS = [
	ActionSpec("list_documents", READ),
	ActionSpec("get_document", READ),
	ActionSpec("create_document", CREATE),
	ActionSpec("delete_document", DELETE),
	ActionSpec("run_report", REPORT),
	ActionSpec("disabled_tool", READ, enabled=False),
	ActionSpec("custom_tool", CUSTOM, is_standard=False, method="app.api.do"),
]


def profile(name, **kwargs):
	kwargs.setdefault("action_types", frozenset({READ}))
	kwargs.setdefault("actions", frozenset({"list_documents", "get_document"}))
	return ProfileSpec(name=name, **kwargs)


class TestFieldRule(unittest.TestCase):
	def test_all_fields_allows_everything(self):
		self.assertTrue(FieldRule().allows("anything"))

	def test_only_listed_hides_others_but_keeps_identity_columns(self):
		rule = FieldRule(ONLY_LISTED, frozenset({"customer"}))
		self.assertTrue(rule.allows("customer"))
		self.assertFalse(rule.allows("grand_total"))
		self.assertTrue(rule.allows("name"))
		self.assertTrue(rule.allows("docstatus"))

	def test_exclude_listed(self):
		rule = FieldRule(EXCLUDE_LISTED, frozenset({"salary"}))
		self.assertFalse(rule.allows("salary"))
		self.assertTrue(rule.allows("employee_name"))

	def test_union_with_all_is_all(self):
		rule = FieldRule(ONLY_LISTED, frozenset({"a"})).union(FieldRule())
		self.assertEqual(rule.mode, ALL_FIELDS)

	def test_union_of_only_lists_is_their_union(self):
		rule = FieldRule(ONLY_LISTED, frozenset({"a"})).union(FieldRule(ONLY_LISTED, frozenset({"b"})))
		self.assertEqual(rule, FieldRule(ONLY_LISTED, frozenset({"a", "b"})))

	def test_union_of_exclusions_hides_only_common_fields(self):
		rule = FieldRule(EXCLUDE_LISTED, frozenset({"a", "b"})).union(FieldRule(EXCLUDE_LISTED, frozenset({"b", "c"})))
		self.assertEqual(rule, FieldRule(EXCLUDE_LISTED, frozenset({"b"})))

	def test_union_of_disjoint_exclusions_is_all(self):
		rule = FieldRule(EXCLUDE_LISTED, frozenset({"a"})).union(FieldRule(EXCLUDE_LISTED, frozenset({"b"})))
		self.assertEqual(rule.mode, ALL_FIELDS)

	def test_union_of_only_and_exclude(self):
		only = FieldRule(ONLY_LISTED, frozenset({"a", "x"}))
		exclude = FieldRule(EXCLUDE_LISTED, frozenset({"a", "b"}))
		self.assertEqual(only.union(exclude), FieldRule(EXCLUDE_LISTED, frozenset({"b"})))
		self.assertEqual(exclude.union(only), FieldRule(EXCLUDE_LISTED, frozenset({"b"})))

	def test_parse_field_list(self):
		self.assertEqual(parse_field_list("a, b\nc\n\n"), frozenset({"a", "b", "c"}))
		self.assertEqual(parse_field_list(None), frozenset())


class TestProfileMatching(unittest.TestCase):
	def test_deny_by_default(self):
		policy = resolve_policy("u@x.com", ["Sales User"], [], ACTIONS)
		self.assertTrue(policy.is_empty)
		self.assertFalse(policy.allows_tool("list_documents"))
		self.assertEqual(policy.doctypes(), [])

	def test_matches_by_role(self):
		p = profile("Sales", roles=frozenset({"Sales User"}))
		policy = resolve_policy("u@x.com", ["Sales User", "Employee"], [p], ACTIONS)
		self.assertEqual(policy.profiles, ("Sales",))

	def test_matches_by_user(self):
		p = profile("Bot", users=frozenset({"bot@x.com"}))
		self.assertEqual(resolve_policy("bot@x.com", [], [p], ACTIONS).profiles, ("Bot",))
		self.assertTrue(resolve_policy("other@x.com", [], [p], ACTIONS).is_empty)

	def test_disabled_profile_never_applies(self):
		p = profile("Sales", roles=frozenset({"Sales User"}), enabled=False)
		self.assertTrue(resolve_policy("u@x.com", ["Sales User"], [p], ACTIONS).is_empty)

	def test_role_mismatch(self):
		p = profile("Sales", roles=frozenset({"Sales User"}))
		self.assertTrue(resolve_policy("u@x.com", ["Stock User"], [p], ACTIONS).is_empty)

	def test_match_reason(self):
		p = profile("Mixed", roles=frozenset({"A", "B"}), users=frozenset({"u@x.com"}))
		self.assertEqual(
			match_reason(p, "u@x.com", ["B"]),
			[{"type": "user", "value": "u@x.com"}, {"type": "role", "value": "B"}],
		)


class TestToolResolution(unittest.TestCase):
	def test_tool_needs_its_action_type_enabled(self):
		p = profile(
			"P",
			users=frozenset({"u"}),
			action_types=frozenset({READ}),
			actions=frozenset({"list_documents", "create_document"}),
		)
		policy = resolve_policy("u", [], [p], ACTIONS)
		self.assertTrue(policy.allows_tool("list_documents"))
		self.assertFalse(policy.allows_tool("create_document"))

	def test_tool_must_be_selected(self):
		p = profile("P", users=frozenset({"u"}), action_types=frozenset({READ, CREATE}), actions=frozenset({"list_documents"}))
		self.assertFalse(resolve_policy("u", [], [p], ACTIONS).allows_tool("create_document"))

	def test_globally_disabled_action_is_hidden(self):
		p = profile("P", users=frozenset({"u"}), actions=frozenset({"disabled_tool"}))
		self.assertFalse(resolve_policy("u", [], [p], ACTIONS).allows_tool("disabled_tool"))

	def test_unknown_action_is_ignored(self):
		p = profile("P", users=frozenset({"u"}), actions=frozenset({"ghost"}))
		self.assertEqual(resolve_policy("u", [], [p], ACTIONS).tools, frozenset())

	def test_type_cap_is_per_profile_not_global(self):
		# Profile A enables Create but not the tool; profile B selects the tool but not the type.
		a = profile("A", users=frozenset({"u"}), action_types=frozenset({READ, CREATE}), actions=frozenset({"list_documents"}))
		b = profile("B", users=frozenset({"u"}), action_types=frozenset({READ}), actions=frozenset({"create_document"}))
		policy = resolve_policy("u", [], [a, b], ACTIONS)
		self.assertFalse(policy.allows_tool("create_document"))
		self.assertEqual(policy.action_types, frozenset({READ, CREATE}))

	def test_custom_action_requires_custom_type(self):
		p = profile("P", users=frozenset({"u"}), action_types=frozenset({READ, CUSTOM}), actions=frozenset({"custom_tool"}))
		self.assertTrue(resolve_policy("u", [], [p], ACTIONS).allows_tool("custom_tool"))


class TestDoctypeGrants(unittest.TestCase):
	def test_operation_requires_row_flag_and_action_type(self):
		rule = DoctypeRule("Customer", frozenset({READ, UPDATE, DELETE}))
		p = profile("P", users=frozenset({"u"}), action_types=frozenset({READ, UPDATE}), doctypes=(rule,))
		policy = resolve_policy("u", [], [p], ACTIONS)
		self.assertEqual(policy.operations_for("Customer"), [READ, UPDATE])
		self.assertIsNone(policy.grant("Customer", DELETE))
		self.assertIsNone(policy.grant("Item", READ))

	def test_doctypes_for_operation(self):
		p = profile(
			"P",
			users=frozenset({"u"}),
			action_types=frozenset({READ, SUBMIT, CANCEL}),
			doctypes=(
				DoctypeRule("Sales Invoice", frozenset({READ, SUBMIT})),
				DoctypeRule("Customer", frozenset({READ})),
			),
		)
		policy = resolve_policy("u", [], [p], ACTIONS)
		self.assertEqual(policy.doctypes_for(READ), ["Customer", "Sales Invoice"])
		self.assertEqual(policy.doctypes_for(SUBMIT), ["Sales Invoice"])
		self.assertEqual(policy.doctypes_for(CANCEL), [])

	def test_scopes_merge_as_or(self):
		a = profile("A", users=frozenset({"u"}), doctypes=(DoctypeRule("Task", frozenset({READ}), scope=(("status", "=", "Open"),)),))
		b = profile("B", users=frozenset({"u"}), doctypes=(DoctypeRule("Task", frozenset({READ}), scope=(("priority", "=", "High"),)),))
		grant = resolve_policy("u", [], [a, b], ACTIONS).grant("Task", READ)
		self.assertEqual(grant.scopes, ((("status", "=", "Open"),), (("priority", "=", "High"),)))

	def test_unscoped_grant_wins(self):
		a = profile("A", users=frozenset({"u"}), doctypes=(DoctypeRule("Task", frozenset({READ}), scope=(("status", "=", "Open"),)),))
		b = profile("B", users=frozenset({"u"}), doctypes=(DoctypeRule("Task", frozenset({READ})),))
		self.assertIsNone(resolve_policy("u", [], [a, b], ACTIONS).grant("Task", READ).scopes)

	def test_identical_scopes_are_deduplicated_even_with_list_values(self):
		scope = (("status", "in", ["Open", "Working"]),)
		a = profile("A", users=frozenset({"u"}), doctypes=(DoctypeRule("Task", frozenset({READ}), scope=scope),))
		b = profile("B", users=frozenset({"u"}), doctypes=(DoctypeRule("Task", frozenset({READ}), scope=scope),))
		self.assertEqual(resolve_policy("u", [], [a, b], ACTIONS).grant("Task", READ).scopes, (scope,))

	def test_field_rules_union_across_profiles(self):
		a = profile("A", users=frozenset({"u"}), doctypes=(DoctypeRule("Employee", frozenset({READ}), FieldRule(ONLY_LISTED, frozenset({"first_name"}))),))
		b = profile("B", users=frozenset({"u"}), doctypes=(DoctypeRule("Employee", frozenset({READ}), FieldRule(ONLY_LISTED, frozenset({"department"}))),))
		rule = resolve_policy("u", [], [a, b], ACTIONS).grant("Employee", READ).field_rule
		self.assertEqual(rule.fields, frozenset({"first_name", "department"}))

	def test_visible_field_rule_spans_operations(self):
		p = profile(
			"P",
			users=frozenset({"u"}),
			action_types=frozenset({READ, CREATE}),
			doctypes=(DoctypeRule("ToDo", frozenset({READ, CREATE}), FieldRule(ONLY_LISTED, frozenset({"description"}))),),
		)
		policy = resolve_policy("u", [], [p], ACTIONS)
		self.assertEqual(policy.visible_field_rule("ToDo").fields, frozenset({"description"}))
		self.assertIsNone(policy.visible_field_rule("Note"))

	def test_notes_are_collected(self):
		p = profile("P", users=frozenset({"u"}), doctypes=(DoctypeRule("Lead", frozenset({READ}), notes="Only B2B"),))
		self.assertEqual(resolve_policy("u", [], [p], ACTIONS).grant("Lead", READ).notes, ("Only B2B",))


class TestReportGrants(unittest.TestCase):
	def test_reports_need_report_type(self):
		p = profile("P", users=frozenset({"u"}), action_types=frozenset({READ}), reports=(ReportRule("General Ledger"),))
		self.assertEqual(resolve_policy("u", [], [p], ACTIONS).reports(), [])

	def test_fixed_filters_override_requested(self):
		p = profile(
			"P",
			users=frozenset({"u"}),
			action_types=frozenset({REPORT}),
			reports=(ReportRule("General Ledger", {"company": "ACME"}),),
		)
		grant = resolve_policy("u", [], [p], ACTIONS).report_grant("General Ledger")
		self.assertEqual(
			grant.resolve_filters({"company": "Other", "from_date": "2024-01-01"}),
			{"company": "ACME", "from_date": "2024-01-01"},
		)

	def test_compatible_option_is_chosen_among_profiles(self):
		a = profile("A", users=frozenset({"u"}), action_types=frozenset({REPORT}), reports=(ReportRule("GL", {"company": "ACME"}),))
		b = profile("B", users=frozenset({"u"}), action_types=frozenset({REPORT}), reports=(ReportRule("GL", {"company": "Globex"}),))
		grant = resolve_policy("u", [], [a, b], ACTIONS).report_grant("GL")
		self.assertEqual(grant.resolve_filters({"company": "Globex"}), {"company": "Globex"})
		self.assertEqual(grant.resolve_filters({"company": "Initech"}), {"company": "ACME"})
		self.assertEqual(grant.resolve_filters({}), {"company": "ACME"})

	def test_unrestricted_grant_forces_nothing(self):
		a = profile("A", users=frozenset({"u"}), action_types=frozenset({REPORT}), reports=(ReportRule("GL", {"company": "ACME"}),))
		b = profile("B", users=frozenset({"u"}), action_types=frozenset({REPORT}), reports=(ReportRule("GL"),))
		grant = resolve_policy("u", [], [a, b], ACTIONS).report_grant("GL")
		self.assertEqual(grant.resolve_filters({"company": "Initech"}), {"company": "Initech"})


if __name__ == "__main__":
	unittest.main()
