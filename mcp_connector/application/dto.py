"""Plain data crossing the boundary between use cases and adapters."""

from dataclasses import dataclass, field
from typing import Any, Optional

TABLE_TYPES = frozenset({"Table", "Table MultiSelect"})
SECRET_TYPES = frozenset({"Password"})
# Read-only fields a client may still set (amending a cancelled document).
SETTABLE_READ_ONLY = frozenset({"amended_from"})


@dataclass(frozen=True)
class FieldInfo:
	fieldname: str
	label: str = ""
	fieldtype: str = "Data"
	options: str = ""
	reqd: bool = False
	read_only: bool = False
	readable: bool = True  # current user has read access to the field's permlevel
	writable: bool = True  # current user has write access to the field's permlevel
	in_list_view: bool = False
	allow_on_submit: bool = False
	description: str = ""

	@property
	def is_table(self) -> bool:
		return self.fieldtype in TABLE_TYPES

	@property
	def child_doctype(self) -> str:
		return self.options if self.is_table else ""


@dataclass(frozen=True)
class DoctypeInfo:
	name: str
	module: str = ""
	is_submittable: bool = False
	issingle: bool = False
	istable: bool = False
	title_field: str = ""
	search_fields: tuple = ()
	autoname: str = ""
	description: str = ""
	has_workflow: bool = False
	fields: tuple = ()  # FieldInfo, layout fields excluded
	child_fields: dict = field(default_factory=dict)  # child doctype -> tuple[FieldInfo]

	def field(self, fieldname: str) -> Optional[FieldInfo]:
		for f in self.fields:
			if f.fieldname == fieldname:
				return f
		return None


@dataclass(frozen=True)
class ReportInfo:
	name: str
	report_type: str
	ref_doctype: str = ""
	module: str = ""
	is_standard: bool = False
	permitted: bool = True
	filters: Optional[list] = None  # None = unknown / defined dynamically
	description: str = ""


@dataclass(frozen=True)
class ServerSettings:
	enabled: bool = True
	server_name: str = "ERPNext MCP"
	instructions: str = ""
	default_page_size: int = 20
	max_page_size: int = 100
	max_report_rows: int = 200
	rate_limit_per_minute: int = 120
	audit_mode: str = "All Requests"
	log_arguments: bool = True
	log_retention_days: int = 30
	allowed_origins: tuple = ()


@dataclass(frozen=True)
class Caller:
	user: str
	full_name: str = ""
	roles: tuple = ()
	client_ip: str = ""
	user_agent: str = ""


@dataclass
class ToolOutcome:
	payload: Any
	summary: str = ""
	document_type: str = ""
	document_name: str = ""
	report: str = ""
