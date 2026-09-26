import frappe


def _queues():
	"""Side effects Frappe runs when the request commits (webhooks, jobs, realtime events)."""
	return (
		(frappe.flags, "enqueue_after_commit"),
		(frappe.local, "realtime_log"),
		(frappe.local, "before_commit"),
	)


class FrappeUnitOfWork:
	"""Wrap one tool call in a savepoint so a failed call leaves no partial writes,
	while the audit entry written afterwards is still committed with the request."""

	def atomic(self, work):
		savepoint = f"mcp_{frappe.generate_hash(length=12)}"  # hex only: a safe SQL identifier
		marks = [(owner, attr, len(getattr(owner, attr, None) or [])) for owner, attr in _queues()]
		frappe.db.savepoint(savepoint)
		try:
			return work()
		except Exception:
			try:
				frappe.db.rollback(save_point=savepoint)
			except Exception:
				# The savepoint is gone if the work committed midway; undo what is still open.
				frappe.db.rollback()
			# A savepoint rollback leaves these queues alone; drop what the failed work added so
			# webhooks and background jobs never fire for records that no longer exist.
			for owner, attr, length in marks:
				queue = getattr(owner, attr, None)
				if isinstance(queue, list):
					del queue[length:]
			raise
