// Gate G7: README instructions agree with the code that implements them.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const read = (p) => readFileSync(join(root, p), "utf8");
const readme = read("README.md");
const failures = [];
const need = (ok, message) => ok || failures.push(message);

const endpoint = (read("mcp_connector/api/mcp.py").match(/ENDPOINT_PATH = "([^"]+)"/) || [])[1];
need(endpoint, "ENDPOINT_PATH not found in api/mcp.py");
const urlMentions = readme.match(/\/api\/method\/[\w.]+/g) || [];
need(urlMentions.length >= 5, "README should show the endpoint in the client snippets");
need(urlMentions.every((u) => u === endpoint), `README endpoint(s) ${[...new Set(urlMentions)]} differ from code ${endpoint}`);

const appName = (read("pyproject.toml").match(/^name = "([^"]+)"/m) || [])[1];
const hooksName = (read("mcp_connector/hooks.py").match(/^app_name = "([^"]+)"/m) || [])[1];
need(appName === "mcp_connector" && hooksName === appName, `app name mismatch: pyproject=${appName} hooks=${hooksName}`);
need(readme.includes(`install-app ${appName}`), "README install-app command uses the wrong app name");
need(/bench get-app --branch main /.test(readme), "README lacks the bench get-app step");
need(readme.includes("mohamedashaibi/mcp_connector"), "README does not name the repository");
const frappeRange = (read("pyproject.toml").match(/^frappe = "([^"]+)"/m) || [])[1];
need(frappeRange === ">=14.0.0,<16.0.0", `pyproject should accept Frappe v14 and v15, got ${frappeRange}`);
need(/Frappe \*\*v14\*\* or \*\*v15\*\*/.test(readme), "README should state the v14/v15 requirement");
for (const branch of ["version-14", "version-15"]) need(readme.includes(`\`${branch}\``), `README Docker table lacks ${branch}`);

const tools = [...read("mcp_connector/domain/catalog.py").matchAll(/name="([a-z_]+)"/g)].map((m) => m[1]);
need(tools.length === 14, `expected 14 built-in tools in catalog.py, found ${tools.length}`);
for (const tool of tools) need(readme.includes(`\`${tool}\``), `README tools table misses ${tool}`);

const settings = JSON.parse(read("mcp_connector/mcp_connector/doctype/mcp_settings/mcp_settings.json"));
const rate = settings.fields.find((f) => f.fieldname === "rate_limit_per_minute").default;
need(readme.includes(`default ${rate} requests/minute`), `README rate-limit default differs from DocType default ${rate}`);

const console_js = read("mcp_connector/mcp_connector/page/mcp_console/mcp_console.js");
// [as written in the README, as written in the console's JS source (objects built then JSON.stringify'd)]
for (const [inReadme, inConsole] of [
	["claude mcp add --transport http", "claude mcp add --transport http"],
	["mcp-remote", "mcp-remote"],
	["Authorization:${AUTH_HEADER}", "Authorization:${AUTH_HEADER}"],
	['"mcpServers"', "mcpServers:"],
	['"type": "http"', 'type: "http"'],
	["text/event-stream", "text/event-stream"],
]) {
	need(readme.includes(inReadme), `README lacks client snippet marker ${inReadme}`);
	need(console_js.includes(inConsole), `console snippets lack ${inConsole}`);
}

if (failures.length) {
	console.error(`docs gate failed:\n- ${failures.join("\n- ")}`);
	process.exit(1);
}
console.log(`DOCS GATE PASSED (endpoint ${endpoint}, ${tools.length} tools, snippets consistent)`);
