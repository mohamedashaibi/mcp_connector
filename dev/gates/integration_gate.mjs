// Gate G4: run the Frappe v14 integration suite on a dedicated test site
// (keeps the dev site clean), plus the unit suite under the bench's Python 3.10.
import { readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { benchShell, up } from "../bench.mjs";

const MIN_INTEGRATION_TESTS = 30;
const TEST_SITE = "mcptest.localhost";
const here = dirname(fileURLToPath(import.meta.url));
const modules = readdirSync(join(here, "..", "..", "mcp_connector", "tests", "integration"))
	.filter((f) => f.startsWith("test_") && f.endsWith(".py"))
	.map((f) => `mcp_connector.tests.integration.${f.slice(0, -3)}`);

await up();
benchShell(`
	set -e
	if [ ! -d sites/${TEST_SITE} ]; then
		bench new-site ${TEST_SITE} --mariadb-root-password admin --admin-password admin --no-mariadb-socket >/dev/null
	fi
	bench --site ${TEST_SITE} list-apps | grep -q mcp_connector || bench --site ${TEST_SITE} install-app mcp_connector >/dev/null
	bench --site ${TEST_SITE} set-config allow_tests true >/dev/null
	bench --site ${TEST_SITE} set-config server_script_enabled true >/dev/null
	bench --site ${TEST_SITE} migrate >/dev/null
`);

let ran = 0;
const problems = [];
for (const module of modules) {
	const res = benchShell(`bench --site ${TEST_SITE} run-tests --module ${module} 2>&1`, { allowFail: true, quiet: true });
	const out = res.stdout + res.stderr;
	const count = Number((out.match(/Ran (\d+) tests?/) || [])[1] || 0);
	const ok = /\n(OK)( \(.*\))?\s*$/m.test(out) && !/FAILED \(/.test(out);
	console.log(`${module}: ${count} test(s) ${ok ? "OK" : "FAILED"}`);
	if (!ok || !count) {
		problems.push(module);
		console.log(out.slice(-6000));
	}
	ran += count;
}

const unit = benchShell(
	"env/bin/python -m unittest discover -s apps/mcp_connector/mcp_connector/tests/unit -t apps/mcp_connector 2>&1",
	{ allowFail: true, quiet: true }
);
const unitOut = unit.stdout + unit.stderr;
const unitOk = unit.status === 0 && /\nOK/.test(unitOut);
console.log(`unit suite under bench Python 3.10: ${(unitOut.match(/Ran (\d+) tests?/) || [])[1] || 0} test(s) ${unitOk ? "OK" : "FAILED"}`);
if (!unitOk) console.log(unitOut.slice(-4000));

if (problems.length || !unitOk || ran < MIN_INTEGRATION_TESTS) {
	console.error(`integration gate failed: ran=${ran} (min ${MIN_INTEGRATION_TESTS}), failing modules: ${problems.join(", ") || "none"}, unit ok=${unitOk}`);
	process.exit(1);
}
console.log(`INTEGRATION GATE PASSED (${ran} integration tests on Frappe v14)`);
