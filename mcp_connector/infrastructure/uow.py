import frappe


class FrappeUnitOfWork:
	"""Wrap one tool call in a savepoint so a failed call leaves no partial writes,
	while the audit entry written afterwards is still committed with the request."""

	def atomic(self, work):
		savepoint = f"mcp_{frappe.generate_hash(length=12)}"  # hex only: a safe SQL identifier
		frappe.db.savepoint(savepoint)
		try:
			return work()
		except Exception:
			try:
				frappe.db.rollback(save_point=savepoint)
			except Exception:
				# The savepoint is gone if the work committed midway; undo what is still open.
				frappe.db.rollback()
			raise
