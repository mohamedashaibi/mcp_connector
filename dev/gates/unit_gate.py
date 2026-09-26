"""Gate G1: run the framework-free unit suite and require a minimum test count."""

import pathlib
import sys
import unittest

MIN_TESTS = 60
ROOT = pathlib.Path(__file__).resolve().parents[2]

sys.path.insert(0, str(ROOT))
suite = unittest.defaultTestLoader.discover(str(ROOT / "mcp_connector" / "tests" / "unit"), top_level_dir=str(ROOT))
result = unittest.TextTestRunner(verbosity=1).run(suite)

if not result.wasSuccessful():
	sys.exit(f"unit suite failed: {len(result.failures)} failure(s), {len(result.errors)} error(s)")
if result.testsRun < MIN_TESTS:
	sys.exit(f"only {result.testsRun} tests ran, expected at least {MIN_TESTS}")
if "frappe" in sys.modules:
	sys.exit("unit suite imported frappe; domain/application must stay framework-free")
print(f"UNIT GATE PASSED ({result.testsRun} tests)")
