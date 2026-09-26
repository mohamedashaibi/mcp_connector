// Gate: the endpoint URL alone is enough for an OAuth MCP client such as a Claude custom
// connector. The official MCP SDK does the discovery, PKCE and token handling; Chromium does
// what the person does (ERPNext login, then Allow on the consent screen).
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { UnauthorizedError } from "@modelcontextprotocol/sdk/client/auth.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { chromium } from "playwright";
import { BASE_URL, ENDPOINT, execute, up } from "../bench.mjs";

// Never contacted: the browser's navigation to it is intercepted to capture the code.
const REDIRECT = "http://127.0.0.1:47831/callback";
const CLAUDE_CALLBACKS = ["https://claude.ai/api/mcp/auth_callback", "https://claude.com/api/mcp/auth_callback"];
const failures = [];
let checks = 0;
const check = (ok, message) => {
	checks += 1;
	if (!ok) failures.push(message);
};

class GateOAuthProvider {
	constructor(clientId, clientSecret) {
		this.info = { client_id: clientId, client_secret: clientSecret };
		this.saved = undefined;
		this.verifier = undefined;
		this.authUrl = undefined;
	}
	get redirectUrl() {
		return REDIRECT;
	}
	get clientMetadata() {
		return { client_name: "oauth-e2e-gate", redirect_uris: [REDIRECT], grant_types: ["authorization_code", "refresh_token"], response_types: ["code"], token_endpoint_auth_method: "client_secret_post" };
	}
	clientInformation() {
		return this.info; // pre-registered, as Claude's "Advanced settings" provides it
	}
	tokens() {
		return this.saved;
	}
	saveTokens(tokens) {
		this.saved = tokens;
	}
	redirectToAuthorization(url) {
		this.authUrl = url;
	}
	saveCodeVerifier(verifier) {
		this.verifier = verifier;
	}
	codeVerifier() {
		return this.verifier;
	}
}

const newClient = async (provider) => {
	const client = new Client({ name: "oauth-e2e-gate", version: "1.0.0" });
	await client.connect(new StreamableHTTPClientTransport(new URL(ENDPOINT), { authProvider: provider }));
	return client;
};

const { frappe: FRAPPE_VERSION } = await up();
const env = execute("mcp_connector.tests.e2e_setup.oauth_setup", { redirect_uri: REDIRECT });
const browser = await chromium.launch();

try {
	// ---- discovery, exactly as a connector sees it
	const anonymous = await fetch(ENDPOINT, {
		method: "POST",
		headers: { "Content-Type": "application/json", Accept: "application/json, text/event-stream" },
		body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "raw", version: "1" } } }),
	});
	check(anonymous.status === 401, `anonymous request should be 401, got ${anonymous.status}`);
	const metadataUrl = ((anonymous.headers.get("www-authenticate") || "").match(/resource_metadata="([^"]+)"/) || [])[1];
	check(metadataUrl === `${BASE_URL}/.well-known/oauth-protected-resource/api/method/mcp_connector.api.mcp.endpoint`, `401 challenge should name the resource metadata, got ${metadataUrl}`);
	const resource = await (await fetch(metadataUrl)).json();
	check(resource.resource === ENDPOINT, `resource metadata names ${resource.resource}, not the endpoint`);
	check(JSON.stringify(resource.authorization_servers) === JSON.stringify([BASE_URL]), `authorization servers: ${JSON.stringify(resource.authorization_servers)}`);
	const server = await (await fetch(`${BASE_URL}/.well-known/oauth-authorization-server`)).json();
	check(server.issuer === BASE_URL && server.code_challenge_methods_supported?.includes("S256"), `authorization server metadata lacks issuer or S256: ${JSON.stringify(server)}`);
	check((await fetch(`${BASE_URL}/.well-known/oauth-unknown`)).status === 404, "unknown /.well-known paths must stay 404 (positive control for the renderer)");

	// ---- the SDK starts the authorization-code flow with PKCE
	const provider = new GateOAuthProvider(env.client_id, env.client_secret);
	let refused = null;
	try {
		await newClient(provider);
	} catch (e) {
		refused = e;
	}
	check(refused instanceof UnauthorizedError, `connecting without tokens should end in UnauthorizedError, got ${refused}`);
	check(provider.authUrl?.searchParams.get("code_challenge_method") === "S256", `authorization URL should use S256 PKCE: ${provider.authUrl}`);
	check(provider.authUrl?.href.startsWith(`${BASE_URL}/api/method/frappe.integrations.oauth2.authorize`), `SDK should use Frappe's authorize endpoint: ${provider.authUrl}`);

	// ---- the person: ERPNext login, then Allow
	const context = await browser.newContext();
	const page = await context.newPage();
	const trail = [];
	let callback = null;
	// The callback is reached through Frappe's 302, which route() cannot intercept; the request
	// itself is enough (the browser then fails to connect, as nothing listens there).
	const reached = new Promise((resolve) =>
		page.on("request", (r) => {
			trail.push(r.url().split("?")[0]);
			if (r.url().startsWith(REDIRECT)) resolve((callback = new URL(r.url())));
		})
	);
	await page.goto(provider.authUrl.href);
	await page.locator("#login_email").fill(env.user);
	await page.locator("#login_password").fill(env.password);
	await page.locator(".btn-login").first().click();
	await page.getByRole("button", { name: "Allow" }).click({ timeout: 20000 });
	await Promise.race([reached, new Promise((resolve) => setTimeout(resolve, 20000))]);
	if (!callback) failures.push(`consent did not reach the client callback; requests: ${trail.join(" -> ")}`);
	const code = callback?.searchParams.get("code");
	check(code, `consent should redirect to the client with a code, got ${callback}`);
	await context.close();

	// ---- code exchange, then a real session as the signed-in person
	const exchange = new StreamableHTTPClientTransport(new URL(ENDPOINT), { authProvider: provider });
	await exchange.finishAuth(code);
	check(provider.saved?.access_token && provider.saved?.refresh_token, "token exchange should yield access and refresh tokens");
	const client = await newClient(provider);
	const tools = (await client.listTools()).tools.map((t) => t.name).sort();
	check(JSON.stringify(tools) === JSON.stringify(env.expected_tools), `OAuth session tools ${tools} differ from the profile's ${env.expected_tools}`);
	const listed = await client.callTool({ name: "list_documents", arguments: { doctype: env.order_doctype, fields: ["company"] } });
	const rows = listed.isError ? null : JSON.parse(listed.content[0].text).data;
	check(rows?.length && rows.every((r) => r.company === "ACME"), `row scope of the profile should apply over OAuth: ${listed.content?.[0]?.text?.slice(0, 200)}`);

	// ---- an expired access token is refreshed without the person noticing
	const firstToken = provider.saved.access_token;
	execute("mcp_connector.tests.e2e_setup.expire_oauth_tokens", { user: env.user });
	const again = (await client.listTools()).tools.map((t) => t.name).sort();
	check(provider.saved.access_token !== firstToken, "an expired access token should have been refreshed");
	check(JSON.stringify(again) === JSON.stringify(tools), "tools after refresh should match");

	// ---- disabling the ERPNext user cuts the session off, although the token stays valid
	execute("mcp_connector.tests.e2e_setup.set_user_enabled", { user: env.user, enabled: 0 });
	let blocked = null;
	try {
		await client.listTools();
	} catch (e) {
		blocked = e;
	} finally {
		execute("mcp_connector.tests.e2e_setup.set_user_enabled", { user: env.user, enabled: 1 });
	}
	check(blocked && /disabled/i.test(String(blocked.message)), `a disabled user must be refused, got ${blocked ? blocked.message : "tools"}`);
	check((await client.listTools()).tools.length === tools.length, "re-enabling the user should restore access (positive control)");
	await client.close();

	// ---- the console provisions the client Claude uses (Administrator)
	const admin = await browser.newContext();
	const adminPage = await admin.newPage();
	await adminPage.request.post(`${BASE_URL}/api/method/login`, { form: { usr: "Administrator", pwd: "admin" } });
	await adminPage.goto(`${BASE_URL}/app`, { waitUntil: "networkidle" });
	const consoleCall = async (method) => {
		const res = await adminPage.request.post(`${BASE_URL}/api/method/mcp_connector.api.console.${method}`, {
			headers: { "X-Frappe-CSRF-Token": await adminPage.evaluate(() => frappe.csrf_token) },
		});
		return { status: res.status(), message: (await res.json()).message };
	};
	const first = await consoleCall("create_claude_connector");
	const second = await consoleCall("create_claude_connector");
	check(first.status === 200 && first.message?.client?.client_id, `console should create the Claude client: ${JSON.stringify(first)}`);
	check(second.message?.client?.client_id === first.message?.client?.client_id, "creating the Claude client twice should return the same client");
	check(JSON.stringify(first.message?.client?.redirect_uris) === JSON.stringify(CLAUDE_CALLBACKS), `Claude client redirect URIs: ${JSON.stringify(first.message?.client?.redirect_uris)}`);
	check(first.message?.connector_url === ENDPOINT, `connector URL shown: ${first.message?.connector_url}`);
	await admin.close();
} catch (e) {
	failures.push(`gate aborted: ${e?.stack || e}`);
} finally {
	await browser.close();
	execute("mcp_connector.tests.e2e_setup.set_enabled", { enabled: env.was_enabled ? 1 : 0 });
}

if (failures.length) {
	console.error(`OAuth e2e gate failed (${failures.length}/${checks}):\n- ${failures.join("\n- ")}`);
	process.exit(1);
}
console.log(`OAUTH E2E GATE PASSED on Frappe ${FRAPPE_VERSION} (${checks} checks: discovery, PKCE sign-in in Chromium, profile tools and scope, refresh, console client)`);
