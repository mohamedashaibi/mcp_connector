"""Access policy: which profiles apply to a user and what they jointly allow.

Deny by default. A user gets MCP access only through enabled profiles that
list one of their roles or the user directly. Grants from several profiles are
unioned, the way Frappe unions role permissions:

* a tool is usable when some matching profile enables the tool *and* its action type;
* a DocType operation is usable when some matching profile ticks it on the DocType
  row *and* enables that action type;
* field visibility and row scope are combined across the profiles that grant the
  same DocType operation (see FieldRule.union and DoctypeGrant.scopes).

Frappe's own role and user permissions are always checked on top of this;
a profile can only narrow what a user could already do.
"""

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .action_types import DOC_OPERATIONS, REPORT
from .filters import ALWAYS_VISIBLE

ALL_FIELDS = "All Fields"
ONLY_LISTED = "Only Listed Fields"
EXCLUDE_LISTED = "Exclude Listed Fields"
FIELD_MODES = (ALL_FIELDS, ONLY_LISTED, EXCLUDE_LISTED)


@dataclass(frozen=True)
class FieldRule:
	mode: str = ALL_FIELDS
	fields: frozenset = frozenset()

	def allows(self, fieldname: str) -> bool:
		if fieldname in ALWAYS_VISIBLE or self.mode == ALL_FIELDS:
			return True
		if self.mode == ONLY_LISTED:
			return fieldname in self.fields
		return fieldname not in self.fields

	def union(self, other: "FieldRule") -> "FieldRule":
		"""Fields visible under either rule."""
		if ALL_FIELDS in (self.mode, other.mode):
			return FieldRule()
		if self.mode == other.mode == ONLY_LISTED:
			return FieldRule(ONLY_LISTED, self.fields | other.fields)
		if self.mode == other.mode == EXCLUDE_LISTED:
			return _exclude(self.fields & other.fields)
		only, exclude = (self, other) if self.mode == ONLY_LISTED else (other, self)
		return _exclude(exclude.fields - only.fields)


def _exclude(fields: frozenset) -> FieldRule:
	return FieldRule(EXCLUDE_LISTED, frozenset(fields)) if fields else FieldRule()


def parse_field_list(raw: Optional[str]) -> frozenset:
	if not raw:
		return frozenset()
	parts = raw.replace(",", "\n").split("\n")
	return frozenset(p.strip() for p in parts if p.strip())


@dataclass(frozen=True)
class DoctypeRule:
	doctype: str
	operations: frozenset
	field_rule: FieldRule = FieldRule()
	scope: tuple = ()  # conjunction of normalized conditions; () = every record
	notes: str = ""


@dataclass(frozen=True)
class ReportRule:
	report: str
	fixed_filters: dict = field(default_factory=dict)
	notes: str = ""


@dataclass(frozen=True)
class ProfileSpec:
	name: str
	enabled: bool = True
	roles: frozenset = frozenset()
	users: frozenset = frozenset()
	action_types: frozenset = frozenset()
	actions: frozenset = frozenset()
	doctypes: tuple = ()
	reports: tuple = ()
	description: str = ""

	def applies_to(self, user: str, roles: Iterable[str]) -> bool:
		if not self.enabled:
			return False
		return user in self.users or bool(self.roles.intersection(roles))


@dataclass(frozen=True)
class ActionSpec:
	name: str
	action_type: str
	enabled: bool = True
	is_standard: bool = True
	title: str = ""
	description: str = ""
	method: str = ""
	input_schema: Optional[dict] = None
	read_only_hint: bool = False
	destructive_hint: bool = False


@dataclass(frozen=True)
class DoctypeGrant:
	"""The merged permission for one (doctype, operation) pair."""

	doctype: str
	operation: str
	field_rule: FieldRule
	scopes: Optional[tuple]  # None = unrestricted, else OR of condition conjunctions
	notes: tuple = ()


@dataclass(frozen=True)
class ReportGrant:
	report: str
	fixed_filter_options: tuple  # one dict per granting profile; {} = nothing forced
	notes: tuple = ()

	def resolve_filters(self, requested: dict) -> dict:
		"""Apply the fixed filters of one granting profile to the requested filters.

		When several profiles grant the report with different fixed filters, the
		first option compatible with what the caller asked for is enforced, so
		the result always satisfies at least one profile completely.
		"""
		requested = dict(requested or {})
		if not self.fixed_filter_options or any(not o for o in self.fixed_filter_options):
			return requested
		chosen = self.fixed_filter_options[0]
		for option in self.fixed_filter_options:
			if all(k not in requested or requested[k] == v for k, v in option.items()):
				chosen = option
				break
		return {**requested, **chosen}


class EffectivePolicy:
	"""Everything one user may do over MCP, resolved from their matching profiles."""

	def __init__(self, user: str, profiles: list, actions: dict):
		self.user = user
		self.profiles = tuple(p.name for p in profiles)
		self.action_types = frozenset().union(*(p.action_types for p in profiles)) if profiles else frozenset()

		tools = set()
		for profile in profiles:
			for name in profile.actions:
				action = actions.get(name)
				if action and action.enabled and action.action_type in profile.action_types:
					tools.add(name)
		self.tools = frozenset(tools)

		# doctype -> operation -> [DoctypeRule, ...] (only rows whose action type is on)
		self._rules: dict = {}
		for profile in profiles:
			for rule in profile.doctypes:
				for operation in rule.operations:
					if operation in profile.action_types:
						self._rules.setdefault(rule.doctype, {}).setdefault(operation, []).append(rule)

		self._reports: dict = {}
		for profile in profiles:
			if REPORT not in profile.action_types:
				continue
			for rule in profile.reports:
				self._reports.setdefault(rule.report, []).append(rule)

	@property
	def is_empty(self) -> bool:
		return not self.profiles

	def allows_tool(self, name: str) -> bool:
		return name in self.tools

	def doctypes(self) -> list:
		return sorted(self._rules)

	def operations_for(self, doctype: str) -> list:
		ops = self._rules.get(doctype, {})
		return [op for op in DOC_OPERATIONS if op in ops]

	def doctypes_for(self, operation: str) -> list:
		return sorted(dt for dt, ops in self._rules.items() if operation in ops)

	def grant(self, doctype: str, operation: str) -> Optional[DoctypeGrant]:
		rules = self._rules.get(doctype, {}).get(operation)
		if not rules:
			return None
		field_rule = rules[0].field_rule
		for rule in rules[1:]:
			field_rule = field_rule.union(rule.field_rule)
		scopes = None
		if all(rule.scope for rule in rules):
			distinct = []  # equality, not hashing: condition values may be lists
			for rule in rules:
				if tuple(rule.scope) not in distinct:
					distinct.append(tuple(rule.scope))
			scopes = tuple(distinct)
		notes = tuple(dict.fromkeys(rule.notes for rule in rules if rule.notes))
		return DoctypeGrant(doctype, operation, field_rule, scopes, notes)

	def visible_field_rule(self, doctype: str) -> Optional[FieldRule]:
		"""Union of the field rules across every operation granted on the doctype."""
		result = None
		for operation in self.operations_for(doctype):
			rule = self.grant(doctype, operation).field_rule
			result = rule if result is None else result.union(rule)
		return result

	def reports(self) -> list:
		return sorted(self._reports)

	def report_grant(self, report: str) -> Optional[ReportGrant]:
		rules = self._reports.get(report)
		if not rules:
			return None
		options = tuple(dict(r.fixed_filters or {}) for r in rules)
		notes = tuple(dict.fromkeys(r.notes for r in rules if r.notes))
		return ReportGrant(report, options, notes)

	def summary(self) -> dict:
		return {
			"profiles": list(self.profiles),
			"action_types": sorted(self.action_types),
			"tools": sorted(self.tools),
			"doctypes": {dt: self.operations_for(dt) for dt in self.doctypes()},
			"reports": self.reports(),
		}


def resolve_policy(user: str, roles: Iterable[str], profiles: Iterable[ProfileSpec], actions: Iterable[ActionSpec]) -> EffectivePolicy:
	roles = set(roles or ())
	matched = sorted((p for p in profiles if p.applies_to(user, roles)), key=lambda p: p.name)
	return EffectivePolicy(user, matched, {a.name: a for a in actions})


def match_reason(profile: ProfileSpec, user: str, roles: Iterable[str]) -> Any:
	"""Why a profile applies to a user (for the access inspector)."""
	reasons = []
	if user in profile.users:
		reasons.append({"type": "user", "value": user})
	for role in sorted(profile.roles.intersection(roles or ())):
		reasons.append({"type": "role", "value": role})
	return reasons
