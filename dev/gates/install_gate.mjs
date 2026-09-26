// Gate G3: a fresh Frappe site (--bench v14|v15) installs the app and ends up with every artifact.
import { benchShell, up } from "../bench.mjs";

const SITE = "mcpinstall.localhost";
const DOCTYPES = [
	"MCP Settings",
	"MCP Profile",
	"MCP Profile Role",
	"MCP Profile User",
	"MCP Profile Action",
	"MCP Profile DocType",
	"MCP Profile Report",
	"MCP Action",
	"MCP Audit Log",
];
const STANDARD = [
	"list_doctypes", "describe_doctype", "list_documents", "get_document", "count_documents", "create_document",
	"update_document", "apply_workflow_action", "delete_document", "submit_document", "cancel_document",
	"list_reports", "describe_report", "run_report",
];

const { frappe: FRAPPE_VERSION } = await up();
const failures = [];
// Arguments are constants defined in this file, never external input.
const siteQuery = (method, kwargs) => {
	const res = benchShell(`bench --site ${SITE} execute ${method} --kwargs '${JSON.stringify(kwargs)}'`);
	const last = res.stdout.trim().split("\n").pop();
	return last ? JSON.parse(last) : null;
};

try {
	benchShell(`bench drop-site ${SITE} --root-password admin --force --no-backup >/dev/null 2>&1 || true`, { allowFail: true });
	const install = benchShell(
		`bench new-site ${SITE} --mariadb-root-password admin --admin-password admin --no-mariadb-socket --install-app mcp_connector 2>&1`,
		{ allowFail: true }
	);
	if (install.status !== 0) failures.push(`install failed:\n${install.stdout.slice(-3000)}`);

	const apps = benchShell(`bench --site ${SITE} list-apps`).stdout;
	if (!/mcp_connector/.test(apps)) failures.push("mcp_connector not listed as installed");

	const doctypes = siteQuery("frappe.get_all", { doctype: "DocType", filters: { module: "MCP Connector" }, pluck: "name" }) || [];
	for (const dt of DOCTYPES) if (!doctypes.includes(dt)) failures.push(`missing DocType ${dt}`);
	if (!siteQuery("frappe.db.exists", { dt: "Page", dn: "mcp-console" })) failures.push("console page missing");
	if (!siteQuery("frappe.db.exists", { dt: "Workspace", dn: "MCP Connector" })) failures.push("workspace missing");
	if (siteQuery("frappe.db.exists", { dt: "Role", dn: "MCP Manager" })) failures.push("the legacy MCP Manager role must not exist");
	const ADMIN_ONLY = ["MCP Settings", "MCP Profile", "MCP Action", "MCP Audit Log"];
	const perms = siteQuery("frappe.get_all", { doctype: "DocPerm", filters: { parent: ["in", ADMIN_ONLY] }, fields: ["parent", "role"], limit_page_length: 0 }) || [];
	for (const dt of ADMIN_ONLY) {
		const roles = [...new Set(perms.filter((p) => p.parent === dt).map((p) => p.role))];
		if (JSON.stringify(roles) !== JSON.stringify(["Administrator"])) failures.push(`${dt} permissions should name only Administrator, got ${JSON.stringify(roles)}`);
	}
	for (const [parenttype, parent] of [["Page", "mcp-console"], ["Workspace", "MCP Connector"]]) {
		const roles = siteQuery("frappe.get_all", { doctype: "Has Role", filters: { parenttype, parent }, pluck: "role" }) || [];
		if (JSON.stringify(roles) !== JSON.stringify(["Administrator"])) failures.push(`${parenttype} ${parent} roles should be only Administrator, got ${JSON.stringify(roles)}`);
	}

	const actions = siteQuery("frappe.get_all", { doctype: "MCP Action", filters: { is_standard: 1, enabled: 1 }, pluck: "name" }) || [];
	const missing = STANDARD.filter((a) => !actions.includes(a));
	if (missing.length || actions.length !== STANDARD.length) failures.push(`standard actions wrong: ${JSON.stringify(actions)}`);

	const migrate = benchShell(`bench --site ${SITE} migrate 2>&1`, { allowFail: true });
	if (migrate.status !== 0) failures.push(`second migrate failed:\n${migrate.stdout.slice(-2000)}`);
	const again = siteQuery("frappe.get_all", { doctype: "MCP Action", filters: { is_standard: 1 }, pluck: "name" }) || [];
	if (again.length !== STANDARD.length) failures.push(`migrate duplicated or lost standard actions: ${again.length}`);
} finally {
	benchShell(`bench drop-site ${SITE} --root-password admin --force --no-backup >/dev/null 2>&1 || true`, { allowFail: true });
}

if (failures.length) {
	console.error(`install gate failed:\n- ${failures.join("\n- ")}`);
	process.exit(1);
}
console.log(`INSTALL GATE PASSED on Frappe ${FRAPPE_VERSION} (${DOCTYPES.length} doctypes, page, workspace, Administrator-only access, ${STANDARD.length} standard actions; migrate idempotent)`);
