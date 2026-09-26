// Gate: a v14 site that already uses the connector moves to v15 the supported way (backup,
// restore on a v15 bench, migrate) and keeps its profiles, actions, settings and audit log.
// Starts both dev benches itself; the v14 dev site is populated by the other gates.
import { join } from "node:path";
import { COMPOSE_FILE, run } from "../bench.mjs";

const SOURCE_SITE = "mcp.localhost";
const TARGET_SITE = "mcpupgrade.localhost";
const BENCH = "/home/frappe/frappe-bench";
const ADMIN_ONLY = ["MCP Settings", "MCP Profile", "MCP Action", "MCP Audit Log"];
const AUDIT_FIELDS = ["modified", "modified_by", "creation", "owner"];

const compose = (v, args, opts) => run("docker", ["compose", "-p", `mcp-connector-${v}`, "-f", COMPOSE_FILE, ...args], opts);
const shell = (v, script, opts) => compose(v, ["exec", "-T", "-u", "frappe", "-w", BENCH, "backend", "bash", "-lc", script], opts);
// Arguments are constants defined in this file, never external input.
const query = (v, site, method, kwargs) => {
	const last = shell(v, `bench --site ${site} execute ${method} --kwargs '${JSON.stringify(kwargs)}'`).stdout.trim().split("\n").pop();
	return last ? JSON.parse(last) : null;
};
const snapshot = (v, site) => ({
	profiles: query(v, site, "frappe.get_all", { doctype: "MCP Profile", fields: ["name", "enabled"], order_by: "name asc" }),
	profile_rows: ["MCP Profile Role", "MCP Profile User", "MCP Profile Action", "MCP Profile DocType", "MCP Profile Report"].map((dt) =>
		query(v, site, "frappe.db.count", { dt })
	),
	actions: query(v, site, "frappe.get_all", { doctype: "MCP Action", fields: ["name", "enabled", "action_type"], order_by: "name asc" }),
	audit_entries: query(v, site, "frappe.db.count", { dt: "MCP Audit Log" }),
	settings: Object.entries(query(v, site, "frappe.db.get_singles_dict", { doctype: "MCP Settings" }) || {})
		.filter(([field]) => !AUDIT_FIELDS.includes(field))
		.sort(([a], [b]) => a.localeCompare(b)),
	permission_roles: [...new Set((query(v, site, "frappe.get_all", { doctype: "DocPerm", filters: { parent: ["in", ADMIN_ONLY] }, pluck: "role" }) || []))],
});

const version = (v) => shell(v, `env/bin/python -c "import frappe; print(frappe.__version__)"`).stdout.trim().split("\n").pop();
for (const v of ["v14", "v15"]) run("node", [join(import.meta.dirname, "..", "bench.mjs"), "up", "--bench", v]);

const failures = [];
const before = snapshot("v14", SOURCE_SITE);
if (!before.profiles?.length || !before.audit_entries) failures.push("positive control: the v14 dev site has no profiles or audit entries to carry over (run the e2e gate first)");

try {
	// Carried between the containers as base64 over stdin/stdout: no host paths involved.
	const backup = shell("v14", `bench --site ${SOURCE_SITE} backup >/dev/null && base64 -w0 "$(ls -t sites/${SOURCE_SITE}/private/backups/*-database.sql.gz | head -1)"`)
		.stdout.trim()
		.split("\n")
		.pop();
	shell("v15", "base64 -d > /tmp/mcp-upgrade.sql.gz", { input: backup });
	shell("v15", `bench drop-site ${TARGET_SITE} --root-password admin --force --no-backup >/dev/null 2>&1 || true`, { allowFail: true });
	const restore = shell(
		"v15",
		`bench new-site ${TARGET_SITE} --mariadb-root-password admin --admin-password admin --no-mariadb-socket >/dev/null 2>&1 && ` +
			`bench --site ${TARGET_SITE} restore /tmp/mcp-upgrade.sql.gz --db-root-password admin 2>&1`,
		{ allowFail: true }
	);
	if (restore.status !== 0) failures.push(`restore on v15 failed:\n${restore.stdout.slice(-3000)}`);
	const migrate = shell("v15", `bench --site ${TARGET_SITE} migrate 2>&1`, { allowFail: true });
	if (migrate.status !== 0) failures.push(`migrate on v15 failed:\n${migrate.stdout.slice(-3000)}`);

	if (!failures.length) {
		const after = snapshot("v15", TARGET_SITE);
		for (const key of Object.keys(before)) {
			if (JSON.stringify(after[key]) !== JSON.stringify(before[key])) failures.push(`${key} changed: v14 ${JSON.stringify(before[key])} vs v15 ${JSON.stringify(after[key])}`);
		}
		if (JSON.stringify(after.permission_roles) !== JSON.stringify(["Administrator"])) failures.push(`permissions after upgrade: ${JSON.stringify(after.permission_roles)}`);
	}
} finally {
	shell("v15", `bench drop-site ${TARGET_SITE} --root-password admin --force --no-backup >/dev/null 2>&1 || true; rm -f /tmp/mcp-upgrade.sql.gz`, { allowFail: true });
}

if (failures.length) {
	console.error(`upgrade gate failed:\n- ${failures.join("\n- ")}`);
	process.exit(1);
}
console.log(
	`UPGRADE GATE PASSED Frappe ${version("v14")} to ${version("v15")} (${before.profiles.length} profiles, ${before.actions.length} actions, ` +
		`${before.audit_entries} audit entries, settings and Administrator-only permissions kept)`
);
