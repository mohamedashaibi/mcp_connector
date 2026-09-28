"""Naming series: what a series may contain and how a new default joins the existing ones."""

import re

from .errors import InvalidArguments

NAMING_SERIES_FIELD = "naming_series"
NAMING_SETTINGS = "Document Naming Settings"  # Frappe changes series through this Single
MAX_SERIES_LENGTH = 100

# Frappe's own NAMING_SERIES_PATTERN, checked here as well so nothing else ever reaches it.
_SERIES = re.compile(r"[\w\- /.#{}]+")

# ERPNext names these by another rule unless a setting selects "Naming Series".
SERIES_SWITCHES = {
	"Customer": ("Selling Settings", "cust_master_name"),
	"Supplier": ("Buying Settings", "supp_master_name"),
	"Item": ("Stock Settings", "item_naming_by"),
}


def with_default_series(current_options: str, series) -> tuple:
	"""(options, user_must_always_select): `series` first as the default, existing series kept after it."""
	series = series.strip() if isinstance(series, str) else ""
	if not series or len(series) > MAX_SERIES_LENGTH or not _SERIES.fullmatch(series):
		raise InvalidArguments(
			f"series must be at most {MAX_SERIES_LENGTH} characters of letters, digits, spaces and - / . # {{ }}, "
			"e.g. \"C-.#####\" or \"INV-.YYYY.-.####\""
		)
	existing = [o.strip() for o in (current_options or "").split("\n")]
	# A blank first option means users must pick a series; keep that as it was.
	must_select = len(existing) > 1 and existing[0] == ""
	return [series, *dict.fromkeys(o for o in existing if o and o != series)], must_select
