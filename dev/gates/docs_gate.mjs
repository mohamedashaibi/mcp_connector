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
need(tools.length === 15, `expected 15 built-in tools in catalog.py, found ${tools.length}`);
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

// Claude connector link: callbacks and the in-Claude steps match the code and the console.
const callbacks = [...(read("mcp_connector/domain/oauth.py").match(/CLAUDE_REDIRECT_URIS = \(([^)]*)\)/) || ["", ""])[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
need(callbacks.length === 2, `expected 2 Claude callback URLs in domain/oauth.py, found ${callbacks.length}`);
for (const url of callbacks) need(readme.includes(url), `README does not list the Claude callback ${url}`);
for (const step of ["Customize → Connectors", "Add custom connector", "Advanced settings", "Organization settings → Connectors → Add → Custom → Web"]) {
	need(readme.includes(step), `README Claude connector steps lack "${step}"`);
	need(console_js.includes(step), `console Claude connector steps lack "${step}"`);
}
need(readme.includes("/.well-known/oauth-authorization-server"), "README does not explain OAuth discovery");

// ChatGPT connector: the redirect URL and the in-ChatGPT steps match the code and the console.
const chatgptRedirect = (read("mcp_connector/domain/oauth.py").match(/CHATGPT_REDIRECT_PREFIX = "([^"]+)"/) || [])[1];
need(chatgptRedirect, "CHATGPT_REDIRECT_PREFIX not found in domain/oauth.py");
need(readme.includes(`${chatgptRedirect}…`), `README does not show the ChatGPT redirect URL ${chatgptRedirect}`);
need(console_js.includes(`CHATGPT_REDIRECT = "${chatgptRedirect}"`), "console's ChatGPT redirect URL differs from domain/oauth.py");
for (const step of ["Developer mode", "Create app", "MCP Server URL", "Allow this redirect URL", "Mismatching redirect URI"]) {
	need(readme.includes(step), `README ChatGPT connector steps lack "${step}"`);
	need(console_js.includes(step), `console ChatGPT connector steps lack "${step}"`);
}

if (failures.length) {
	console.error(`docs gate failed:\n- ${failures.join("\n- ")}`);
	process.exit(1);
}
console.log(`DOCS GATE PASSED (endpoint ${endpoint}, ${tools.length} tools, snippets consistent)`);
