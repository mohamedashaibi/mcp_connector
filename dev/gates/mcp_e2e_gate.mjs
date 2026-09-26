// Gate G5: the official MCP TypeScript SDK talks to the app over Streamable HTTP
// with Frappe token auth, and every customization dimension is observable on the wire.
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { ENDPOINT, execute, up } from "../bench.mjs";

const ORDER = "MCP Test Order";
const REQUEST = "MCP Test Request";
const REPORT = "MCP Test Orders By Company";
const failures = [];
let checks = 0;
const check = (ok, message) => {
	checks += 1;
	if (!ok) failures.push(message);
};

async function raw(auth, body, headers = {}) {
	const res = await fetch(ENDPOINT, {
		method: "POST",
		headers: {
			"Content-Type": "application/json",
			Accept: "application/json, text/event-stream",
			...(auth ? { Authorization: `token ${auth}` } : {}),
			...headers,
		},
		body: JSON.stringify(body),
	});
	let json = null;
	try {
		json = await res.json();
	} catch {
		// 202 has no body
	}
	return { status: res.status, json };
}

async function connect(auth) {
	const client = new Client({ name: "mcp-connector-e2e-gate", version: "1.0.0" });
	const transport = new StreamableHTTPClientTransport(new URL(ENDPOINT), {
		requestInit: { headers: { Authorization: `token ${auth}` } },
	});
	await client.connect(transport);
	return client;
}

const payload = (result) => (result.isError ? null : JSON.parse(result.content[0].text));
const enumOf = (tool, prop = "doctype") => (tool && tool.inputSchema.properties[prop] ? tool.inputSchema.properties[prop].enum : null);

const { frappe: FRAPPE_VERSION } = await up();
const env = execute("mcp_connector.tests.e2e_setup.setup");
const initialize = { jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "raw", version: "1" } } };

try {
	// ---- authentication and access boundaries (raw HTTP to see status codes)
	const anonymous = await raw(null, initialize);
	check(anonymous.status === 401, `anonymous request should be 401, got ${anonymous.status}`);
	const orphan = await raw(env.orphan, initialize);
	check(orphan.status === 403 && /No MCP access profile/.test(orphan.json?.error?.message || ""), `unprofiled user should be 403, got ${orphan.status}`);
	const wrongSecret = await raw(env.sales.split(":")[0] + ":wrong", initialize);
	check(wrongSecret.status === 401 || wrongSecret.status === 403, `bad secret should be rejected, got ${wrongSecret.status}`);
	const notification = await raw(env.sales, { jsonrpc: "2.0", method: "notifications/initialized" });
	check(notification.status === 202, `notification should be 202, got ${notification.status}`);
	const get = await fetch(ENDPOINT, { headers: { Authorization: `token ${env.sales}` } });
	check(get.status === 405, `GET should be 405, got ${get.status}`);

	// ---- sales user: profile matched through its ERPNext role
	const sales = await connect(env.sales);
	const server = sales.getServerVersion();
	check(server && server.name === "erpnext-mcp-connector", `unexpected serverInfo ${JSON.stringify(server)}`);
	check(/mcp-sales@example\.com/.test(sales.getInstructions() || ""), "instructions should name the acting user");
	const tools = Object.fromEntries((await sales.listTools()).tools.map((t) => [t.name, t]));
	for (const name of ["list_documents", "get_document", "create_document", "update_document", "run_report"]) {
		check(Boolean(tools[name]), `sales should have ${name}`);
	}
	check(!tools.count_documents, "count_documents is not selected in the profile, so it must be hidden (per-action toggle)");
	check(!tools.delete_document, "Delete action type is off, so delete_document must be hidden");
	check(JSON.stringify(enumOf(tools.list_documents)) === JSON.stringify([ORDER]), `doctype enum should be [${ORDER}], got ${JSON.stringify(enumOf(tools.list_documents))}`);
	check(JSON.stringify(enumOf(tools.run_report, "report_name")) === JSON.stringify([REPORT]), "report enum should list only the granted report");
	check(tools.list_documents.annotations.readOnlyHint === true && tools.create_document.annotations.readOnlyHint === false, "tool annotations missing");

	// row scope + hidden fields
	const listed = await sales.callTool({ name: "list_documents", arguments: { doctype: ORDER, fields: ["name", "customer", "company"], limit: 100 } });
	const rows = payload(listed)?.data || [];
	check(rows.length > 0 && rows.every((r) => r.company === "ACME"), "list must only return ACME orders (row scope)");
	check(!rows.some((r) => r.name === env.globex_order), "the Globex order must be invisible");
	check(listed.structuredContent && Array.isArray(listed.structuredContent.data), "structuredContent should be present for 2025-06-18 clients");
	const hiddenField = await sales.callTool({ name: "list_documents", arguments: { doctype: ORDER, fields: ["secret_note"] } });
	check(hiddenField.isError === true, "requesting a hidden field must fail");
	const doc = payload(await sales.callTool({ name: "get_document", arguments: { doctype: ORDER, name: env.acme_order } }));
	check(doc && doc.customer === "E2E Acme" && !("secret_note" in doc) && !("internal_code" in doc), "get_document must hide secret_note and permlevel fields");
	const outOfScope = await sales.callTool({ name: "get_document", arguments: { doctype: ORDER, name: env.globex_order } });
	check(outOfScope.isError === true && /not found or is outside your access scope/.test(outOfScope.content[0].text), "out-of-scope record must look missing");
	const described = payload(await sales.callTool({ name: "describe_doctype", arguments: { doctype: ORDER } }));
	const fieldNames = (described?.fields || []).map((f) => f.fieldname);
	check(fieldNames.includes("customer") && !fieldNames.includes("secret_note"), "describe_doctype must hide excluded fields");

	// writes inside/outside scope, and an ungranted doctype
	const created = await sales.callTool({ name: "create_document", arguments: { doctype: ORDER, data: { customer: "E2E Created", company: "ACME", items: [{ item: "Nut", qty: 5 }] } } });
	const createdDoc = payload(created);
	check(createdDoc && createdDoc.name && createdDoc.items?.[0]?.item === "Nut", `create inside scope should work: ${created.content[0].text.slice(0, 200)}`);
	const escaped = await sales.callTool({ name: "create_document", arguments: { doctype: ORDER, data: { customer: "E2E Escape", company: "Globex" } } });
	check(escaped.isError === true && /outside your access scope/.test(escaped.content[0].text), "create outside scope must be refused");
	if (createdDoc) {
		const updated = await sales.callTool({ name: "update_document", arguments: { doctype: ORDER, name: createdDoc.name, data: { amount: 42 } } });
		check(payload(updated)?.amount === 42, "update inside scope should work");
	}
	const ungranted = await sales.callTool({ name: "list_documents", arguments: { doctype: REQUEST } });
	check(ungranted.isError === true, "a DocType outside the profile must be refused");
	let unknownTool = false;
	try {
		const r = await sales.callTool({ name: "delete_document", arguments: { doctype: ORDER, name: env.acme_order } });
		unknownTool = r.isError === true;
	} catch (e) {
		unknownTool = /Unknown tool/.test(String(e.message));
	}
	check(unknownTool, "calling a tool outside the profile must fail as an unknown tool");

	// report with enforced fixed filter
	const report = payload(await sales.callTool({ name: "run_report", arguments: { report_name: REPORT, filters: { company: "Globex" } } }));
	check(report && report.filters_applied.company === "ACME", "fixed report filter must override the client's value");
	check(report && report.rows.length > 0 && report.rows.every((r) => r.company === "ACME"), "report rows must follow the fixed filter");
	await sales.close();

	// ---- ERPNext permissions narrow the profile (viewer role cannot create)
	const viewer = await connect(env.viewer);
	const viewerTools = (await viewer.listTools()).tools.map((t) => t.name);
	check(viewerTools.includes("list_documents") && !viewerTools.includes("create_document"), `viewer tools wrong: ${viewerTools}`);
	await viewer.close();

	// ---- action-type cap: Create switched off although action + operation are selected
	const capped = await connect(env.capped);
	const cappedTools = (await capped.listTools()).tools.map((t) => t.name);
	check(cappedTools.includes("list_documents") && !cappedTools.includes("create_document"), `capped tools wrong: ${cappedTools}`);
	await capped.close();

	// ---- master switch
	execute("mcp_connector.tests.e2e_setup.set_enabled", { enabled: 0 });
	const paused = await raw(env.sales, initialize);
	check(paused.status === 403 && /disabled/.test(paused.json?.error?.message || ""), `paused server should answer 403, got ${paused.status}`);
} finally {
	execute("mcp_connector.tests.e2e_setup.set_enabled", { enabled: env.was_enabled ? 1 : 0 });
}

if (failures.length) {
	console.error(`MCP e2e gate failed (${failures.length}/${checks}):\n- ${failures.join("\n- ")}`);
	process.exit(1);
}
console.log(`MCP E2E GATE PASSED on Frappe ${FRAPPE_VERSION} (${checks} checks via @modelcontextprotocol/sdk)`);
