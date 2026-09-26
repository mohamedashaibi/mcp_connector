"""Gate G2: architecture and safety rules that can be checked without running the app.

1. mcp_connector/domain and mcp_connector/application import no framework code.
2. Every Python file parses with the Python 3.10 grammar (Frappe v14's floor).
3. No SQL is built from interpolated strings passed to frappe.db.sql / .sql().

Each detector first proves itself on a known-bad snippet (negative control),
so a silently broken detector cannot certify a clean tree.
"""

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "mcp_connector"
PURE_LAYERS = (PACKAGE / "domain", PACKAGE / "application")
FORBIDDEN_ROOTS = {"frappe", "erpnext", "werkzeug", "pymysql", "redis"}


def framework_imports(tree: ast.AST) -> list:
	found = []
	for node in ast.walk(tree):
		if isinstance(node, ast.Import):
			names = [alias.name for alias in node.names]
		elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
			names = [node.module]
		else:
			continue
		found += [n for n in names if n.split(".")[0] in FORBIDDEN_ROOTS]
	return found


def interpolated_sql(tree: ast.AST) -> list:
	"""Calls like frappe.db.sql(f"...{x}...") or db.sql("..." % x) / .format(...)."""
	hits = []
	for node in ast.walk(tree):
		if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("sql", "multisql")):
			continue
		if not node.args:
			continue
		query = node.args[0]
		dynamic = (
			isinstance(query, ast.JoinedStr)
			or (isinstance(query, ast.BinOp) and isinstance(query.op, (ast.Mod, ast.Add)))
			or (isinstance(query, ast.Call) and isinstance(query.func, ast.Attribute) and query.func.attr == "format")
		)
		if dynamic:
			hits.append(node.lineno)
	return hits


def parses_as_310(source: str, path: str) -> bool:
	try:
		ast.parse(source, filename=path, feature_version=(3, 10))
		return True
	except SyntaxError:
		return False


def self_test() -> None:
	bad_import = ast.parse("import frappe\nfrom frappe.utils import cint\n")
	assert framework_imports(bad_import) == ["frappe", "frappe.utils"], "import detector is broken"
	assert framework_imports(ast.parse("import json\nfrom .dto import X\n")) == [], "import detector false positive"
	bad_sql = ast.parse('frappe.db.sql(f"select * from t where a={x}")\nfrappe.db.sql("x=%s" % y)\ndb.sql("{}".format(z))\n')
	assert interpolated_sql(bad_sql) == [1, 2, 3], "SQL detector is broken"
	assert interpolated_sql(ast.parse('frappe.db.sql("select 1 where a=%s", (x,))\n')) == [], "SQL detector false positive"
	assert not parses_as_310("type Alias = int\n", "control.py"), "3.10 grammar check is broken"
	assert parses_as_310("match x:\n    case 1:\n        pass\n", "control.py"), "3.10 grammar check rejects valid code"


def main() -> None:
	self_test()
	problems = []
	files = sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)
	for path in files:
		source = path.read_text(encoding="utf-8")
		rel = path.relative_to(ROOT).as_posix()
		if not parses_as_310(source, rel):
			problems.append(f"{rel}: does not parse with the Python 3.10 grammar")
			continue
		tree = ast.parse(source)
		if any(layer in path.parents for layer in PURE_LAYERS):
			for name in framework_imports(tree):
				problems.append(f"{rel}: pure layer imports framework module {name}")
		for line in interpolated_sql(tree):
			problems.append(f"{rel}:{line}: SQL built from an interpolated string")

	pure = [p for p in files if any(layer in p.parents for layer in PURE_LAYERS)]
	if len(pure) < 10:
		problems.append(f"expected at least 10 files in domain/application, found {len(pure)}")
	if problems:
		sys.exit("static gate failed:\n" + "\n".join(problems))
	print(f"STATIC GATE PASSED ({len(files)} files, {len(pure)} in pure layers)")


if __name__ == "__main__":
	main()
