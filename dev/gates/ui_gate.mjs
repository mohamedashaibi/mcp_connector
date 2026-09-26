// Gate G6: the MCP Console works in a real browser against the v14 bench.
// Builds a profile entirely through the UI, checks what the server stored,
// deletes it through the UI, and writes light/dark/narrow screenshots.
import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { BASE_URL, up } from "../bench.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const SHOTS = join(here, "..", "artifacts", "ui");
mkdirSync(SHOTS, { recursive: true });
const PROFILE = `UI Gate ${Date.now().toString(36)}`;
const failures = [];
const check = (ok, message) => {
	if (!ok) failures.push(message);
};

await up();
const browser = await chromium.launch();

async function session({ theme = "Light", width = 1440 } = {}) {
	const context = await browser.newContext({ viewport: { width, height: 1000 } });
	const page = await context.newPage();
	const errors = [];
	page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
	page.on("console", (m) => {
		if (m.type() === "error" && !/favicon|Failed to load resource/.test(m.text())) errors.push(`console: ${m.text()}`);
	});
	const login = await page.request.post(`${BASE_URL}/api/method/login`, { form: { usr: "Administrator", pwd: "admin" } });
	check(login.ok(), "login failed");
	await page.request.post(`${BASE_URL}/api/method/frappe.core.doctype.user.user.switch_theme`, { form: { theme } });
	await page.goto(`${BASE_URL}/app/mcp-console`, { waitUntil: "networkidle" });
	await page.waitForTimeout(2000); // first desk boot clears its cache and reloads once
	await page.goto(`${BASE_URL}/app/mcp-console`, { waitUntil: "networkidle" });
	await page.locator(".mcpc-display").waitFor();
	return { context, page, errors };
}

const api = async (page, method, args = {}) => {
	const res = await page.request.post(`${BASE_URL}/api/method/mcp_connector.api.console.${method}`, {
		headers: { "X-Frappe-CSRF-Token": await page.evaluate(() => frappe.csrf_token) },
		form: Object.fromEntries(Object.entries(args).map(([k, v]) => [k, typeof v === "string" ? v : JSON.stringify(v)])),
	});
	return (await res.json()).message;
};

async function pick_link(scope, value) {
	const input = scope.locator("input").first();
	await input.click();
	await input.fill(value);
	const option = scope.page().locator(".awesomplete ul[role=listbox]:visible li", { hasText: value }).first();
	await option.waitFor({ timeout: 10000 });
	await option.click();
	await scope.page().waitForTimeout(400);
}

async function nav(page, section) {
	await page.locator(`.mcpc-nav[data-section="${section}"]`).click();
	await page.waitForLoadState("networkidle");
	await page.waitForTimeout(500);
}

async function shoot(page, name) {
	await page.screenshot({ path: join(SHOTS, `${name}.png`), fullPage: true });
}

// ---------------------------------------------------------------- light, desktop: full workflow
{
	const { context, page, errors } = await session();
	const expectations = {
		overview: ".mcpc-kpis",
		profiles: ".mcpc-profiles",
		actions: ".mcpc-alist",
		inspector: ".mcpc-inspect-pick",
		activity: ".mcpc-filters",
		connect: ".mcpc-flow",
		settings: ".mcpc-settings",
	};
	for (const [section, selector] of Object.entries(expectations)) {
		await nav(page, section);
		check((await page.locator(selector).count()) > 0, `section ${section} did not render ${selector}`);
		check(page.url().includes(`/app/mcp-console/${section}`), `section ${section} did not deep-link (url ${page.url()})`);
		await shoot(page, `light-desktop-${section}`);
	}

	// New profile through the dialog, starting from the read-only preset.
	await nav(page, "profiles");
	await page.locator('[data-act="new-profile"]').first().click();
	const dialog = page.locator(".modal:visible");
	await dialog.locator('input[data-fieldname="profile_name"]').fill(PROFILE);
	await dialog.locator(".btn-modal-primary").click();
	await page.locator(".mcpc-ed__name").waitFor();
	check((await page.locator(".mcpc-ed__name").inputValue()) === PROFILE, "editor did not open with the new profile name");
	check(await page.locator(".mcpc-editor .mcpc-savebar").isVisible(), "save bar not shown for an unsaved profile");

	// Who: a role.
	await pick_link(page.locator('[data-adder="roles"]'), "Blogger");
	check((await page.locator(".mcpc-chip", { hasText: "Blogger" }).count()) === 1, "role chip not added");

	// Capabilities: switch Create on (selects its action automatically).
	await page.locator('[data-act="editor-tab"][data-tab="capabilities"]').click();
	await page.locator('[data-act="toggle-type"][data-type="Create"]').click();
	check((await page.locator('[data-act="toggle-type"][data-type="Create"]').getAttribute("aria-checked")) === "true", "Create type not switched on");
	check((await page.locator('[data-act="toggle-action"][data-action="create_document"]').getAttribute("aria-pressed")) === "true", "create_document not selected");
	await shoot(page, "light-desktop-profile-capabilities");

	// DocTypes: ToDo with Create, fields excluding assigned_by, scope status != Cancelled.
	await page.locator('[data-act="editor-tab"][data-tab="doctypes"]').click();
	await pick_link(page.locator('[data-adder="doctype"]'), "ToDo");
	await page.locator('.mcpc-matrix tbody tr', { hasText: "ToDo" }).waitFor();
	await page.locator('[data-act="toggle-op"][data-op="Create"][data-row="0"]').click();
	await page.locator('[data-act="edit-fields"][data-row="0"]').click();
	const fields = page.locator(".modal:visible");
	await fields.locator('[data-seg="field-mode"][data-value="Exclude Listed Fields"]').click();
	await fields.locator('[data-k="pick"][data-field="assigned_by"]').click();
	await page.waitForTimeout(200);
	await shoot(page, "light-desktop-dialog-fields");
	await fields.locator(".btn-modal-primary").click();
	await page.waitForTimeout(400);
	await page.locator('[data-act="edit-scope"][data-row="0"]').click();
	const scope = page.locator(".modal:visible");
	await scope.locator('[data-k="add"]').click();
	await scope.locator('.mcpc-cond select[data-k="field"]').selectOption("status");
	await scope.locator('.mcpc-cond select[data-k="op"]').selectOption("!=");
	await scope.locator('.mcpc-cond input[data-k="value"]').fill("Cancelled");
	await scope.locator('textarea[data-k="notes"]').fill("Open work only");
	await shoot(page, "light-desktop-dialog-scope");
	await scope.locator(".btn-modal-primary").click();
	await page.waitForTimeout(400);
	check((await page.locator('[data-act="edit-scope"][data-row="0"]').innerText()).includes("1 condition"), "scope button does not show the condition");
	await shoot(page, "light-desktop-profile-doctypes");

	// Reports: one report with a fixed filter.
	await page.locator('[data-act="editor-tab"][data-tab="reports"]').click();
	await pick_link(page.locator('[data-adder="report"]'), "Permitted Documents For User");
	await page.locator('[data-act="add-fixed"][data-row="0"]').click();
	await page.locator('[data-bind="profile.fixed.0.0:key"]').fill("doctype");
	await page.locator('[data-bind="profile.fixed.0.0:value"]').fill("ToDo");
	await shoot(page, "light-desktop-profile-reports");

	// Save with the keyboard shortcut.
	await page.keyboard.press("Control+s");
	await page.waitForFunction((name) => decodeURIComponent(location.pathname).endsWith(`/profiles/${name}`), PROFILE, { timeout: 15000 });
	await page.locator(".mcpc-ed__name").waitFor();
	await page.waitForTimeout(800);
	check(await page.locator(".mcpc-editor .mcpc-savebar").isHidden(), "save bar still visible after saving");

	const saved = await api(page, "get_profile", { name: PROFILE });
	check(saved && saved.roles.includes("Blogger"), "stored profile lacks the Blogger role");
	check(saved && ["Read", "Create", "Report"].every((t) => saved.action_types.includes(t)), `stored action types wrong: ${saved && saved.action_types}`);
	check(saved && saved.actions.includes("create_document") && saved.actions.includes("run_report"), "stored actions lack create_document/run_report");
	const todo = saved && saved.doctypes.find((d) => d.doctype === "ToDo");
	check(todo && todo.operations.includes("Read") && todo.operations.includes("Create"), "ToDo operations not stored");
	check(todo && todo.field_mode === "Exclude Listed Fields" && todo.field_list.join() === "assigned_by", "ToDo field rule not stored");
	check(todo && JSON.stringify(todo.scope) === JSON.stringify([["status", "!=", "Cancelled"]]), `ToDo scope not stored: ${todo && JSON.stringify(todo.scope)}`);
	check(todo && todo.notes === "Open work only", "ToDo notes not stored");
	const rep = saved && saved.reports.find((r) => r.report === "Permitted Documents For User");
	check(rep && rep.fixed_filters && rep.fixed_filters.doctype === "ToDo", "report fixed filter not stored");

	// AI view shows the tools this profile produces.
	await page.locator('[data-act="editor-tab"][data-tab="preview"]').click();
	await page.locator(".mcpc-tools").waitFor({ timeout: 15000 });
	check((await page.locator(".mcpc-tool__name", { hasText: "create_document" }).count()) === 1, "AI view lacks create_document");
	await shoot(page, "light-desktop-profile-preview");

	// Inspector explains an unprofiled user.
	await page.goto(`${BASE_URL}/app/mcp-console/inspector/Guest`, { waitUntil: "networkidle" });
	await page.goto(`${BASE_URL}/app/mcp-console/inspector/Administrator`, { waitUntil: "networkidle" });
	await page.locator(".mcpc-person").waitFor({ timeout: 15000 });

	// Connect snippets switch clients.
	await nav(page, "connect");
	await page.locator('[data-seg="client"][data-value="cursor"]').click();
	check((await page.locator(".mcpc-snippet pre").innerText()).includes('"mcpServers"'), "Cursor snippet not shown");
	await page.locator('[data-seg="client"][data-value="claude_code"]').click();
	check((await page.locator(".mcpc-snippet pre").innerText()).includes("claude mcp add --transport http"), "Claude Code snippet not shown");
	await page.locator('[data-act="run-test"]').click();
	await page.locator(".mcpc-checks li.is-ok, .mcpc-checks li.is-bad").first().waitFor({ timeout: 15000 });
	await shoot(page, "light-desktop-connect-tested");

	// Delete through the UI.
	await page.goto(`${BASE_URL}/app/mcp-console/profiles/${encodeURIComponent(PROFILE)}`, { waitUntil: "networkidle" });
	await page.locator('[data-act="delete-profile"]').click();
	await page.locator(".modal:visible .btn-modal-primary").click();
	await page.waitForFunction(() => location.pathname.replace(/\/$/, "").endsWith("/mcp-console/profiles"), null, { timeout: 15000 });
	await page.waitForLoadState("networkidle");
	check((await page.locator(".mcpc-pitem", { hasText: PROFILE }).count()) === 0, "deleted profile still listed");
	const gone = await api(page, "profiles");
	check(Array.isArray(gone) && !gone.some((p) => p.name === PROFILE), "profile still exists after UI delete");

	check(errors.length === 0, `page errors (light): ${errors.join(" | ")}`);
	await context.close();
}

// ---------------------------------------------------------------- dark + narrow renders
for (const [theme, width, label] of [["Dark", 1440, "dark-desktop"], ["Light", 390, "light-narrow"], ["Dark", 390, "dark-narrow"]]) {
	const { context, page, errors } = await session({ theme, width });
	for (const section of ["overview", "profiles", "actions", "activity", "connect", "settings"]) {
		await page.goto(`${BASE_URL}/app/mcp-console/${section}`, { waitUntil: "networkidle" });
		await page.waitForTimeout(700);
		check((await page.locator(".mcpc-main").innerText()).length > 50, `${label} ${section} rendered empty`);
		if (width < 500) {
			const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
			check(overflow <= 1, `${label} ${section} scrolls horizontally by ${overflow}px`);
		}
		await shoot(page, `${label}-${section}`);
	}
	check(errors.length === 0, `page errors (${label}): ${errors.join(" | ")}`);
	await context.close();
}

await browser.close();
if (failures.length) {
	console.error(`UI gate failed:\n- ${failures.join("\n- ")}`);
	process.exit(1);
}
console.log(`UI GATE PASSED (screenshots in ${SHOTS})`);
