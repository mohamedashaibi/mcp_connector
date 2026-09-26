// Helpers (and a tiny CLI) for the disposable Frappe benches in dev/docker.
// Pick the version with `--bench v14` (default) or `--bench v15`; each runs as its own
// compose project with its own volumes and port, so both can run side by side.
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const BENCHES = {
	v14: { project: "mcp-connector-v14", image: "frappe/erpnext:v14.92.12", port: 8014 },
	v15: { project: "mcp-connector-v15", image: "frappe/erpnext:v15.121.4", port: 8015 },
};
const flag = process.argv.indexOf("--bench");
export const BENCH_NAME = flag > -1 ? process.argv[flag + 1] : process.env.MCP_BENCH || "v14";
export const BENCH = BENCHES[BENCH_NAME];
if (!BENCH) throw new Error(`unknown bench "${BENCH_NAME}"; use one of: ${Object.keys(BENCHES).join(", ")}`);

const here = dirname(fileURLToPath(import.meta.url));
export const COMPOSE_FILE = join(here, "docker", "compose.yml");
export const SITE = "mcp.localhost";
export const BASE_URL = `http://localhost:${BENCH.port}`;
export const ENDPOINT = `${BASE_URL}/api/method/mcp_connector.api.mcp.endpoint`;

export function run(cmd, args, { allowFail = false, input, quiet = false, env } = {}) {
	const res = spawnSync(cmd, args, { encoding: "utf8", input, maxBuffer: 64 * 1024 * 1024, env: env || process.env });
	if (!quiet && res.stderr && res.status !== 0) process.stderr.write(res.stderr);
	if (res.status !== 0 && !allowFail) {
		throw new Error(`${cmd} ${args.join(" ")} failed (${res.status}):\n${res.stdout}\n${res.stderr}`);
	}
	return res;
}

const composeEnv = { ...process.env, ERPNEXT_IMAGE: BENCH.image, MCP_BENCH_PORT: String(BENCH.port) };
export const compose = (args, opts = {}) =>
	run("docker", ["compose", "-p", BENCH.project, "-f", COMPOSE_FILE, ...args], { ...opts, env: composeEnv });

/** Run a shell command in the backend container as the frappe user, inside the bench. */
export const benchShell = (script, opts) =>
	compose(["exec", "-T", "-u", "frappe", "-w", "/home/frappe/frappe-bench", "backend", "bash", "-lc", script], opts);

export function execute(method, kwargs) {
	const args = kwargs ? ` --kwargs '${JSON.stringify(kwargs).replace(/'/g, "'\\''")}'` : "";
	const res = benchShell(`bench --site ${SITE} execute ${method}${args}`);
	const last = res.stdout.trim().split("\n").pop();
	return last ? JSON.parse(last) : null; // bench prints nothing for falsy return values
}

export async function waitForHttp(url = `${BASE_URL}/api/method/ping`, timeoutMs = 120000) {
	const deadline = Date.now() + timeoutMs;
	while (Date.now() < deadline) {
		try {
			const res = await fetch(url);
			if (res.ok) return;
		} catch {}
		await new Promise((r) => setTimeout(r, 1000));
	}
	throw new Error(`${url} did not answer within ${timeoutMs} ms`);
}

export async function restartServer() {
	compose(["exec", "-d", "-u", "frappe", "backend", "bash", "/home/frappe/frappe-bench/apps/mcp_connector/dev/docker/serve.sh"]);
	await new Promise((r) => setTimeout(r, 2500)); // let the old process die before polling
}

/** Start the bench, set up the site, and return the Frappe/Python versions it really runs. */
export async function up() {
	compose(["up", "-d"]);
	const setup = benchShell("bash apps/mcp_connector/dev/docker/setup.sh");
	if (!setup.stdout.includes("SETUP DONE")) throw new Error(setup.stdout + setup.stderr);
	const [frappe, python] = benchShell(`env/bin/python -c "import sys, frappe; print(frappe.__version__); print(sys.version.split()[0])"`)
		.stdout.trim()
		.split("\n")
		.slice(-2);
	if (frappe.split(".")[0] !== BENCH_NAME.slice(1)) throw new Error(`bench ${BENCH_NAME} runs Frappe ${frappe}`);
	await restartServer();
	await waitForHttp();
	return { frappe, python };
}

export function down() {
	compose(["down", "-v", "--remove-orphans"], { allowFail: true });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
	const command = process.argv[2];
	if (command === "up") await up().then((v) => console.log(`${BENCH_NAME} bench (Frappe ${v.frappe}) ready at ${BASE_URL} (Administrator / admin)`));
	else if (command === "down") down();
	else if (command === "restart") {
		await restartServer();
		await waitForHttp();
		console.log(`${BENCH_NAME} server restarted`);
	} else console.log("usage: node dev/bench.mjs up|down|restart [--bench v14|v15]");
}
