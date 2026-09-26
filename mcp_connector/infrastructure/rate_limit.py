import time

import frappe


def within_rate_limit(user: str, per_minute: int) -> bool:
	"""Fixed one-minute window per user, counted in Redis."""
	if per_minute <= 0:
		return True
	cache = frappe.cache()
	key = cache.make_key(f"mcp_connector:rate:{user}:{int(time.time() // 60)}")
	count = cache.incrby(key, 1)
	if count == 1:
		cache.expire(key, 120)
	return count <= per_minute
