"""Use cases behind the MCP Console admin page."""

from datetime import datetime, timedelta
from typing import Optional, Protocol

from mcp_connector.domain.action_types import ACTION_TYPES, DOC_OPERATIONS, FRAPPE_PTYPE, READ
from mcp_connector.domain.audit import DENIED, ERROR, SUCCESS
from mcp_connector.domain.catalog import BUILTIN_TOOLS
from mcp_connector.domain.errors import InvalidArguments, NotFound
from mcp_connector.domain.policy import ALL_FIELDS, EffectivePolicy, match_reason, resolve_policy
from mcp_connector.domain.protected import NEVER_EXPOSED, READ_ONLY, is_exposable, is_writable

from .console_dto import ActionPayload, LogQuery, ProfilePayload, SettingsPayload
from .dto import ServerSettings
from .mcp_server import ToolCatalog


class ProfileRepository(Protocol):
	def summaries(self) -> list: ...

	def get(self, name: str) -> Optional[dict]: ...

	def save(self, payload: ProfilePayload) -> str: ...

	def delete(self, name: str) -> None: ...

	def duplicate(self, name: str, new_name: str) -> str: ...

	def specs(self) -> list: ...

	def action_specs(self) -> list: ...


class ActionRepository(Protocol):
	def list(self) -> list: ...

	def save(self, payload: ActionPayload) -> str: ...

	def delete(self, name: str) -> None: ...


class SettingsRepository(Protocol):
	def get(self) -> dict: ...

	def server_settings(self) -> ServerSettings: ...

	def save(self, payload: SettingsPayload) -> None: ...


class AuditQuery(Protocol):
	def entries_since(self, hours: int, limit: int) -> tuple: ...  # (now "YYYY-mm-dd HH:MM:SS", rows)

	def search(self, query: LogQuery) -> list: ...

	def get(self, name: str) -> Optional[dict]: ...


class Directory(Protocol):
	def exists(self, user: str) -> bool: ...

	def roles_of(self, user: str) -> list: ...

	def full_name(self, user: str) -> str: ...

	def is_enabled(self, user: str) -> bool: ...

	def count_enabled_users(self, roles: tuple, users: tuple) -> int: ...

	def has_permission(self, doctype: str, ptype: str, user: str) -> bool: ...

	def report_permitted(self, report: str, user: str) -> bool: ...

	def generate_keys(self, user: str) -> dict: ...


class CatalogRepository(Protocol):
	def doctypes(self) -> list: ...  # [{name, module, issingle, is_submittable, custom}]

	def reports(self) -> list: ...  # [{name, module, report_type, ref_doctype}]


# -- catalogs for the bulk pickers ------------------------------------------------


class ListDoctypeCatalog:
	"""Every DocType a profile could grant, grouped-ready, with its MCP restrictions."""

	def __init__(self, catalog: CatalogRepository):
		self.catalog = catalog

	def execute(self) -> list:
		return [
			{**row, "writable": is_writable(row["name"])}
			for row in self.catalog.doctypes()
			if is_exposable(row["name"])
		]


class ListReportCatalog:
	def __init__(self, catalog: CatalogRepository):
		self.catalog = catalog

	def execute(self) -> list:
		return list(self.catalog.reports())


# -- dashboard ------------------------------------------------------------------


def _parse_ts(value: str) -> datetime:
	return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")


class GetDashboard:
	def __init__(self, audit: AuditQuery):
		self.audit = audit

	def execute(self, hours: int = 24) -> dict:
		if hours not in (24, 168, 720):
			raise InvalidArguments("hours must be 24, 168 or 720")
		now, rows = self.audit.entries_since(hours, limit=50_000)
		# ponytail: aggregates in Python over at most 50k rows; move to SQL GROUP BY if logs outgrow that.
		now_dt = _parse_ts(now)
		hourly = hours <= 48
		step = timedelta(hours=1) if hourly else timedelta(days=1)
		fmt = "%Y-%m-%d %H" if hourly else "%Y-%m-%d"
		count = hours if hourly else hours // 24
		start = now_dt.replace(minute=0, second=0) if hourly else now_dt.replace(hour=0, minute=0, second=0)
		labels = [(start - step * i).strftime(fmt) for i in reversed(range(count))]
		buckets = {label: {SUCCESS: 0, DENIED: 0, ERROR: 0} for label in labels}

		totals = {SUCCESS: 0, DENIED: 0, ERROR: 0}
		tools, users, durations = {}, {}, []
		for row in rows:
			status = row.get("status")
			if status not in totals:
				continue
			totals[status] += 1
			key = _parse_ts(row["creation"]).strftime(fmt)
			if key in buckets:
				buckets[key][status] += 1
			if row.get("tool"):
				tools[row["tool"]] = tools.get(row["tool"], 0) + 1
			if row.get("user"):
				users[row["user"]] = users.get(row["user"], 0) + 1
			if status == SUCCESS and row.get("duration_ms") is not None:
				durations.append(int(row["duration_ms"]))

		total = sum(totals.values())
		durations.sort()
		return {
			"hours": hours,
			"total": total,
			"success": totals[SUCCESS],
			"denied": totals[DENIED],
			"errors": totals[ERROR],
			"success_rate": round(totals[SUCCESS] * 100 / total, 1) if total else None,
			"median_ms": durations[len(durations) // 2] if durations else None,
			"p95_ms": durations[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else None,
			"series": {
				"labels": labels,
				SUCCESS: [buckets[l][SUCCESS] for l in labels],
				DENIED: [buckets[l][DENIED] for l in labels],
				ERROR: [buckets[l][ERROR] for l in labels],
			},
			"top_tools": sorted(tools.items(), key=lambda kv: (-kv[1], kv[0]))[:6],
			"top_users": sorted(users.items(), key=lambda kv: (-kv[1], kv[0]))[:6],
			"recent": rows[:8],
			"truncated": len(rows) >= 50_000,
		}


class GetBootstrap:
	def __init__(self, settings: SettingsRepository, profiles: ProfileRepository, actions: ActionRepository):
		self.settings = settings
		self.profiles = profiles
		self.actions = actions

	def execute(self) -> dict:
		return {
			"settings": self.settings.get(),
			"profiles": self.profiles.summaries(),
			"actions": self.actions.list(),
			"action_types": list(ACTION_TYPES),
			"operations": list(DOC_OPERATIONS),
			"builtin": {t.name: {"title": t.title, "description": t.description} for t in BUILTIN_TOOLS},
			"protected": {"never": sorted(NEVER_EXPOSED), "read_only": sorted(READ_ONLY)},
		}


# -- profiles -------------------------------------------------------------------


class GetProfile:
	def __init__(self, profiles: ProfileRepository, directory: Directory):
		self.profiles = profiles
		self.directory = directory

	def execute(self, name: str) -> dict:
		profile = self.profiles.get(name)
		if profile is None:
			raise NotFound(f"MCP Profile {name} does not exist")
		profile["matched_users"] = self.directory.count_enabled_users(tuple(profile["roles"]), tuple(profile["users"]))
		return profile


class SaveProfile:
	def __init__(self, profiles: ProfileRepository):
		self.profiles = profiles

	def execute(self, payload: ProfilePayload) -> str:
		return self.profiles.save(payload)


class DeleteProfile:
	def __init__(self, profiles: ProfileRepository):
		self.profiles = profiles

	def execute(self, name: str) -> None:
		if self.profiles.get(name) is None:
			raise NotFound(f"MCP Profile {name} does not exist")
		self.profiles.delete(name)


class DuplicateProfile:
	def __init__(self, profiles: ProfileRepository):
		self.profiles = profiles

	def execute(self, name: str, new_name: str) -> str:
		new_name = (new_name or "").strip()
		if not new_name:
			raise InvalidArguments("Enter a name for the copy")
		if self.profiles.get(name) is None:
			raise NotFound(f"MCP Profile {name} does not exist")
		return self.profiles.duplicate(name, new_name)


def _tool_rows(descriptors: list, action_specs: dict) -> list:
	rows = []
	for d in descriptors:
		props = d["inputSchema"].get("properties", {})
		spec = action_specs.get(d["name"])
		rows.append(
			{
				"name": d["name"],
				"title": d["title"],
				"description": d["description"],
				"action_type": spec.action_type if spec else "",
				"is_standard": bool(spec and spec.is_standard),
				"doctypes": props.get("doctype", {}).get("enum"),
				"reports": props.get("report_name", {}).get("enum"),
				"read_only": d["annotations"]["readOnlyHint"],
				"destructive": d["annotations"]["destructiveHint"],
				"input_schema": d["inputSchema"],
			}
		)
	return rows


class PreviewProfile:
	"""What an AI client would see through this one profile (before ERPNext role checks)."""

	def __init__(self, profiles: ProfileRepository, settings: SettingsRepository, catalog: ToolCatalog):
		self.profiles = profiles
		self.settings = settings
		self.catalog = catalog

	def execute(self, name: str) -> dict:
		spec = next((p for p in self.profiles.specs() if p.name == name), None)
		if spec is None:
			raise NotFound(f"MCP Profile {name} does not exist")
		actions = {a.name: a for a in self.profiles.action_specs()}
		policy = EffectivePolicy("preview", [spec], actions)
		descriptors = self.catalog.describe(
			policy, self.settings.server_settings(), actions, allowed=lambda dt, ptype: True, report_allowed=lambda r: True
		)
		return {"profile": name, "enabled": spec.enabled, "tools": _tool_rows(descriptors, actions)}


class InspectUser:
	"""Explain exactly what one user can do over MCP, and why."""

	def __init__(
		self,
		profiles: ProfileRepository,
		settings: SettingsRepository,
		directory: Directory,
		catalog: ToolCatalog,
	):
		self.profiles = profiles
		self.settings = settings
		self.directory = directory
		self.catalog = catalog

	def execute(self, user: str) -> dict:
		if not user or not self.directory.exists(user):
			raise NotFound(f"User {user} does not exist")
		roles = self.directory.roles_of(user)
		specs = self.profiles.specs()
		actions = {a.name: a for a in self.profiles.action_specs()}
		policy = resolve_policy(user, roles, specs, actions.values())

		cache: dict = {}

		def allowed(doctype: str, ptype: str) -> bool:
			key = (doctype, ptype)
			if key not in cache:
				cache[key] = self.directory.has_permission(doctype, ptype, user)
			return cache[key]

		report_cache: dict = {}

		def report_allowed(report: str) -> bool:
			if report not in report_cache:
				report_cache[report] = self.directory.report_permitted(report, user)
			return report_cache[report]

		matched = []
		for spec in specs:
			reasons = match_reason(spec, user, roles)
			if reasons:
				matched.append({"name": spec.name, "enabled": spec.enabled, "reasons": reasons})

		doctypes = []
		for doctype in policy.doctypes():
			granted = policy.operations_for(doctype)
			cells = {}
			for op in DOC_OPERATIONS:
				if op not in granted:
					cells[op] = "none"
				elif not is_exposable(doctype) or (op != READ and not is_writable(doctype)):
					cells[op] = "locked"
				elif not allowed(doctype, FRAPPE_PTYPE[op]):
					cells[op] = "erp_denied"
				else:
					cells[op] = "allowed"
			doctypes.append(
				{
					"doctype": doctype,
					"operations": cells,
					"scoped": any(policy.grant(doctype, op).scopes for op in granted),
					"fields_limited": policy.visible_field_rule(doctype).mode != ALL_FIELDS,
				}
			)

		reports = [{"report": r, "permitted": report_allowed(r)} for r in policy.reports()]
		descriptors = self.catalog.describe(
			policy, self.settings.server_settings(), actions, allowed=allowed, report_allowed=report_allowed
		)
		return {
			"user": user,
			"full_name": self.directory.full_name(user),
			"enabled": self.directory.is_enabled(user),
			"roles": sorted(roles),
			"has_access": not policy.is_empty,
			"profiles": matched,
			"action_types": sorted(policy.action_types, key=ACTION_TYPES.index),
			"tools": _tool_rows(descriptors, actions),
			"doctypes": doctypes,
			"reports": reports,
		}


# -- actions, settings, logs, keys ----------------------------------------------


class SaveAction:
	def __init__(self, actions: ActionRepository):
		self.actions = actions

	def execute(self, payload: ActionPayload) -> str:
		return self.actions.save(payload)


class DeleteAction:
	def __init__(self, actions: ActionRepository):
		self.actions = actions

	def execute(self, name: str) -> None:
		self.actions.delete(name)


class SaveSettings:
	def __init__(self, settings: SettingsRepository):
		self.settings = settings

	def execute(self, payload: SettingsPayload) -> dict:
		if payload.default_page_size > payload.max_page_size:
			raise InvalidArguments("Default page size cannot exceed the maximum page size")
		self.settings.save(payload)
		return self.settings.get()


class SearchLogs:
	def __init__(self, audit: AuditQuery):
		self.audit = audit

	def execute(self, query: LogQuery) -> dict:
		rows = self.audit.search(query)
		return {"rows": rows[: query.limit], "has_more": len(rows) > query.limit, "start": query.start}


class GetLog:
	def __init__(self, audit: AuditQuery):
		self.audit = audit

	def execute(self, name: str) -> dict:
		entry = self.audit.get(name)
		if entry is None:
			raise NotFound("Audit log entry not found")
		return entry


class GenerateKeys:
	def __init__(self, directory: Directory):
		self.directory = directory

	def execute(self, user: str) -> dict:
		if not user or not self.directory.exists(user):
			raise NotFound(f"User {user} does not exist")
		if user in ("Guest",):
			raise InvalidArguments("Guest cannot have API keys")
		return self.directory.generate_keys(user)

