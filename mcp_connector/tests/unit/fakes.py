"""In-memory stand-ins for the ports, so use cases run without a Frappe site."""

import copy
import re

from mcp_connector.application.dto import DoctypeInfo, FieldInfo, ReportInfo
from mcp_connector.domain.errors import ErpFailure


def _matches(row: dict, condition) -> bool:
	fieldname, op, value = condition
	actual = row.get(fieldname)
	if op == "=":
		return actual == value
	if op == "!=":
		return actual != value
	if op in (">", "<", ">=", "<="):
		if actual is None:
			return False
		return {">": actual > value, "<": actual < value, ">=": actual >= value, "<=": actual <= value}[op]
	if op in ("like", "not like"):
		pattern = "^" + re.escape(value).replace("%", ".*").replace("_", ".") + "$"
		hit = bool(re.match(pattern, str(actual or ""), re.IGNORECASE))
		return hit if op == "like" else not hit
	if op == "in":
		return actual in value
	if op == "not in":
		return actual not in value
	if op == "is":
		return (actual not in (None, "")) if value == "set" else (actual in (None, ""))
	if op == "between":
		return actual is not None and value[0] <= actual <= value[1]
	raise AssertionError(op)


class FakeGateway:
	def __init__(self):
		self.infos = {}
		self.docs = {}  # doctype -> name -> dict
		self.denied = set()  # (doctype, ptype) the "ERP roles" refuse
		self.doc_denied = set()  # (doctype, name) refused like a User Permission would
		self.reports = {}  # name -> (ReportInfo, rows)
		self.workflows = {}  # (doctype, name) -> {"state","docstatus","actions"}
		self.methods = {}
		self.messages = []
		self.calls = []
		self.last_report_filters = None

	# -- setup helpers -------------------------------------------------
	def add_doctype(self, name, fields, **kwargs):
		self.infos[name] = DoctypeInfo(name=name, module=kwargs.pop("module", "Test"), fields=tuple(fields), **kwargs)
		self.docs.setdefault(name, {})

	def add_doc(self, doctype, name, **values):
		self.docs[doctype][name] = {"name": name, "doctype": doctype, "docstatus": 0, "modified": "2024-01-01", **values}

	# -- port ------------------------------------------------------------
	def doctype_info(self, doctype):
		return self.infos.get(doctype)

	def has_permission(self, doctype, ptype, user=None):
		return doctype in self.infos and (doctype, ptype) not in self.denied

	def _rows(self, doctype, filters, or_filters):
		rows = [r for r in self.docs.get(doctype, {}).values() if all(_matches(r, c) for c in filters)]
		if or_filters:
			rows = [r for r in rows if any(_matches(r, c) for c in or_filters)]
		return rows

	def list_documents(self, doctype, fields, filters, or_filters, order_by, start, limit):
		self.calls.append(("list", doctype, list(filters), list(or_filters)))
		rows = self._rows(doctype, filters, or_filters)
		for part in reversed(order_by.split(",")):
			fieldname, direction = part.strip().replace("`", "").split()
			rows.sort(key=lambda r: (r.get(fieldname) is not None, r.get(fieldname) or ""), reverse=direction == "desc")
		return [{f: r.get(f) for f in fields} for r in rows[start : start + limit]]

	def count_documents(self, doctype, filters, or_filters):
		return len(self._rows(doctype, filters, or_filters))

	def list_names(self, doctype, filters, limit):
		return [r["name"] for r in self._rows(doctype, filters, [])][:limit]

	def document_exists(self, doctype, name):
		return name in self.docs.get(doctype, {})

	def document_matches(self, doctype, name, conditions):
		row = self.docs.get(doctype, {}).get(name)
		return row is not None and all(_matches(row, c) for c in conditions)

	def get_document(self, doctype, name):
		if (doctype, "read") in self.denied or (doctype, name) in self.doc_denied:
			raise ErpFailure("permission", "No permission for this record")
		return copy.deepcopy(self.docs[doctype][name])

	def create_document(self, doctype, data):
		if "fail" in data.values():
			raise ErpFailure("validation", "Validation failed: bad value")
		name = data.get("name") or f"{doctype[:3].upper()}-{len(self.docs[doctype]) + 1:04d}"
		self.add_doc(doctype, name, **{k: v for k, v in data.items() if k != "name"})
		return copy.deepcopy(self.docs[doctype][name])

	def update_document(self, doctype, name, data):
		self.docs[doctype][name].update(data)
		return copy.deepcopy(self.docs[doctype][name])

	def delete_document(self, doctype, name):
		del self.docs[doctype][name]

	def submit_document(self, doctype, name):
		self.docs[doctype][name]["docstatus"] = 1
		return copy.deepcopy(self.docs[doctype][name])

	def cancel_document(self, doctype, name):
		self.docs[doctype][name]["docstatus"] = 2
		return copy.deepcopy(self.docs[doctype][name])

	def workflow_actions(self, doctype, name):
		return copy.deepcopy(self.workflows.get((doctype, name), {"state": None, "docstatus": 0, "actions": []}))

	def apply_workflow_action(self, doctype, name, action):
		workflow = self.workflows[(doctype, name)]
		transition = next(t for t in workflow["actions"] if t["action"] == action)
		self.docs[doctype][name]["workflow_state"] = transition["next_state"]
		self.docs[doctype][name]["docstatus"] = transition["next_docstatus"]
		return copy.deepcopy(self.docs[doctype][name])

	def report_info(self, report):
		entry = self.reports.get(report)
		return entry[0] if entry else None

	def run_report(self, report, filters, limit):
		self.last_report_filters = dict(filters)
		rows = [r for r in self.reports[report][1] if all(r.get(k) == v for k, v in filters.items() if k in r)]
		return {"columns": [{"fieldname": "name", "label": "Name"}], "rows": rows[:limit], "total_rows": len(rows)}

	def call_method(self, method, arguments):
		return self.methods[method](**arguments)

	def drain_messages(self):
		messages, self.messages = self.messages, []
		return messages


class FakeAudit:
	def __init__(self):
		self.entries = []

	def record(self, entry):
		self.entries.append(entry)


class FakeUnitOfWork:
	"""Snapshots the fake gateway's documents and restores them on failure."""

	def __init__(self, gateway):
		self.gateway = gateway
		self.rollbacks = 0

	def atomic(self, work):
		snapshot = copy.deepcopy(self.gateway.docs)
		try:
			return work()
		except Exception:
			self.gateway.docs = snapshot
			self.rollbacks += 1
			raise


def field(fieldname, fieldtype="Data", **kwargs):
	return FieldInfo(fieldname=fieldname, label=fieldname.replace("_", " ").title(), fieldtype=fieldtype, **kwargs)


def report(name, report_type="Script Report", **kwargs):
	return ReportInfo(name=name, report_type=report_type, **kwargs)
