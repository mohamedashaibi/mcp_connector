// Helpers (and a tiny CLI) for the disposable v14 bench in dev/docker.
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
export const COMPOSE_FILE = join(here, "docker", "compose.yml");
export const SITE = "mcp.localhost";
export const BASE_URL = "http://localhost:8014";
export const ENDPOINT = `${BASE_URL}/api/method/mcp_connector.api.mcp.endpoint`;

export function run(cmd, args, { allowFail = false, input, quiet = false } = {}) {
	const res = spawnSync(cmd, args, { encoding: "utf8", input, maxBuffer: 64 * 1024 * 1024 });
	if (!quiet && res.stderr && res.status !== 0) process.stderr.write(res.stderr);
	if (res.status !== 0 && !allowFail) {
		throw new Error(`${cmd} ${args.join(" ")} failed (${res.status}):\n${res.stdout}\n${res.stderr}`);
	}
	return res;
}

export const compose = (args, opts) => run("docker", ["compose", "-f", COMPOSE_FILE, ...args], opts);

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

export async function up() {
	compose(["up", "-d"]);
	const setup = benchShell("bash apps/mcp_connector/dev/docker/setup.sh");
	if (!setup.stdout.includes("SETUP DONE")) throw new Error(setup.stdout + setup.stderr);
	await restartServer();
	await waitForHttp();
}

export function down() {
	compose(["down", "-v", "--remove-orphans"], { allowFail: true });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
	const command = process.argv[2];
	if (command === "up") await up().then(() => console.log(`bench ready at ${BASE_URL} (Administrator / admin)`));
	else if (command === "down") down();
	else if (command === "restart") {
		await restartServer();
		await waitForHttp();
		console.log("server restarted");
	} else console.log("usage: node dev/bench.mjs up|down|restart");
}
