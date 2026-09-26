import frappe


def _queues():
	"""Side effects Frappe runs when the request commits (webhooks, jobs, realtime events).

	v14 keeps them in lists on frappe.flags / frappe.local; v15 registers flush callbacks on
	frappe.db.before_commit / after_commit (CallbackManager deques). Both are covered, and on
	each version the other's attributes simply do not exist."""
	return (
		(frappe.flags, "enqueue_after_commit"),  # v14 background jobs
		(frappe.local, "realtime_log"),  # v14 realtime events
		(frappe.local, "before_commit"),  # v14 commit callbacks (webhook flush)
		(frappe.local, "_webhook_queue"),  # v14 + v15 webhooks
		(frappe.local, "_realtime_log"),  # v15 realtime events
		(getattr(frappe.db, "before_commit", None), "_functions"),  # v15 commit callbacks
		(getattr(frappe.db, "after_commit", None), "_functions"),  # v15 jobs, webhook/realtime flush
	)


def _snapshot():
	marks = []
	for owner, attr in _queues():
		if owner is not None:
			queue = getattr(owner, attr, None)
			marks.append((owner, attr, None if queue is None else len(queue)))
	return marks


def _restore(marks):
	for owner, attr, length in marks:
		queue = getattr(owner, attr, None)
		if queue is None:
			continue
		if length is None:
			# The failed work created this queue. Remove it, so the next write re-registers
			# the flush callback that is being dropped from the commit hooks below.
			try:
				delattr(owner, attr)
				continue
			except (AttributeError, KeyError):
				length = 0
		while len(queue) > length:
			queue.pop()


class FrappeUnitOfWork:
	"""Wrap one tool call in a savepoint so a failed call leaves no partial writes,
	while the audit entry written afterwards is still committed with the request."""

	def atomic(self, work):
		savepoint = f"mcp_{frappe.generate_hash(length=12)}"  # hex only: a safe SQL identifier
		marks = _snapshot()
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
			_restore(marks)
			raise
