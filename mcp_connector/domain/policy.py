"""Access policy: which profiles apply to a user and what they allow.

Deny by default. A user gets MCP access only through enabled profiles that
list one of their roles or the user directly.

Several matching profiles are alternatives, never blended: a call is allowed
when ONE profile allows all of it together -- the tool, its action type, the
DocType operation, the requested fields and the record's scope. Mixing one
profile's fields with another profile's rows (or one profile's tool with
another profile's DocType grant) would grant access no profile describes.

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
class Grant:
	"""One profile's permission for one (doctype, operation) pair."""

	profile: str
	doctype: str
	operation: str
	field_rule: FieldRule
	scope: tuple = ()  # conjunction of conditions; () = every record
	notes: str = ""


def union_rules(grants) -> Optional[FieldRule]:
	result = None
	for grant in grants:
		result = grant.field_rule if result is None else result.union(grant.field_rule)
	return result


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
	"""Everything one user may do over MCP, kept per matching profile."""

	def __init__(self, user: str, profiles: list, actions: dict):
		self.user = user
		self.profiles = tuple(p.name for p in profiles)
		self.action_types = frozenset().union(*(p.action_types for p in profiles)) if profiles else frozenset()

		# Tools each profile makes usable: selected, enabled globally, action type on.
		self._profile_tools = {}
		for profile in profiles:
			self._profile_tools[profile.name] = frozenset(
				name
				for name in profile.actions
				if (action := actions.get(name)) and action.enabled and action.action_type in profile.action_types
			)
		self.tools = frozenset().union(*self._profile_tools.values()) if profiles else frozenset()

		# (doctype, operation) -> [Grant, ...], one per granting profile (action type must be on)
		self._grants: dict = {}
		for profile in profiles:
			for rule in profile.doctypes:
				for operation in rule.operations:
					if operation in profile.action_types:
						self._grants.setdefault((rule.doctype, operation), []).append(
							Grant(profile.name, rule.doctype, operation, rule.field_rule, tuple(rule.scope), rule.notes)
						)

		# report -> [(profile, ReportRule), ...] for profiles with the Report type on
		self._reports: dict = {}
		for profile in profiles:
			if REPORT in profile.action_types:
				for rule in profile.reports:
					self._reports.setdefault(rule.report, []).append((profile.name, rule))

	@property
	def is_empty(self) -> bool:
		return not self.profiles

	def allows_tool(self, name: str) -> bool:
		return name in self.tools

	def _has_tool(self, profile: str, tool: Optional[str]) -> bool:
		return tool is None or tool in self._profile_tools.get(profile, ())

	def grants(self, doctype: str, operation: str, tool: Optional[str] = None) -> list:
		"""Grants for the operation, limited to profiles that also enable `tool` (when given)."""
		return [g for g in self._grants.get((doctype, operation), []) if self._has_tool(g.profile, tool)]

	def doctypes(self) -> list:
		return sorted({doctype for doctype, _ in self._grants})

	def operations_for(self, doctype: str, tool: Optional[str] = None) -> list:
		return [op for op in DOC_OPERATIONS if self.grants(doctype, op, tool)]

	def usable_operations(self, doctype: str, operation_tools: dict) -> list:
		"""Operations granted by a profile that also enables a tool performing them."""
		return [
			op
			for op in DOC_OPERATIONS
			if any(self.grants(doctype, op, tool) for tool in operation_tools.get(op, ()))
		]

	def doctypes_for(self, operation: str, tool: Optional[str] = None) -> list:
		return sorted({dt for (dt, op) in self._grants if op == operation and self.grants(dt, op, tool)})

	def visible_field_rule(self, doctype: str) -> Optional[FieldRule]:
		"""Union of field rules over every grant on the doctype: what describe_doctype may name."""
		return union_rules(g for op in DOC_OPERATIONS for g in self.grants(doctype, op))

	def reports(self, tool: Optional[str] = None) -> list:
		return sorted(r for r, rows in self._reports.items() if any(self._has_tool(p, tool) for p, _ in rows))

	def report_grant(self, report: str, tool: Optional[str] = None) -> Optional[ReportGrant]:
		rules = [rule for profile, rule in self._reports.get(report, []) if self._has_tool(profile, tool)]
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
