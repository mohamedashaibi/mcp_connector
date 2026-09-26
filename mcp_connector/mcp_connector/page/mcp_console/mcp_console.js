// MCP Console — configure which AI clients may do what in ERPNext.
// Vanilla JS on Frappe desk primitives; all server calls go to mcp_connector.api.console.
//
// Escaping rule for this file: markup is built with template literals and every
// interpolated value passes through esc(). Values handed to Frappe helpers that
// render HTML (msgprint, confirm, show_alert, dialog titles) are escaped too.

frappe.provide("mcp_connector");

frappe.pages["mcp-console"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("MCP Console"), single_column: true });
	wrapper.mcp_console = new mcp_connector.Console(page, wrapper);
};

frappe.pages["mcp-console"].on_page_show = function (wrapper) {
	if (wrapper.mcp_console) wrapper.mcp_console.on_show();
};

(() => {
	const API = "mcp_connector.api.console.";
	const OPS = ["Read", "Create", "Update", "Delete", "Submit", "Cancel"];
	const TYPES = ["Read", "Create", "Update", "Delete", "Submit", "Cancel", "Report", "Custom"];
	const OPERATORS = ["=", "!=", ">", "<", ">=", "<=", "like", "not like", "in", "not in", "is", "between"];
	const NUMERIC = new Set(["Int", "Float", "Currency", "Percent", "Check"]);
	const SECTIONS = [
		["overview", __("Overview")],
		["profiles", __("Profiles")],
		["actions", __("Actions")],
		["inspector", __("Inspector")],
		["activity", __("Activity")],
		["connect", __("Connect")],
		["settings", __("Settings")],
	];
	const TYPE_INFO = {
		Read: [__("Read"), __("Look up, list, count and describe records.")],
		Create: [__("Create"), __("Create new records.")],
		Update: [__("Update"), __("Edit records and move them through workflows.")],
		Delete: [__("Delete"), __("Permanently delete records.")],
		Submit: [__("Submit"), __("Submit drafts, posting their ledger and stock effects.")],
		Cancel: [__("Cancel"), __("Cancel submitted documents.")],
		Report: [__("Reports"), __("Run the reports this profile allows.")],
		Custom: [__("Custom actions"), __("Call your own whitelisted methods or API Server Scripts.")],
	};
	const STATUS_CLASS = { Success: "ok", Denied: "warn", Error: "bad" };
	const CHART_COLORS = ["#2f9d58", "#d69e2e", "#e24c4c"];

	const PATHS = {
		check: '<path d="M20 6 9 17l-5-5"/>',
		x: '<path d="M18 6 6 18M6 6l12 12"/>',
		copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
		lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
		plus: '<path d="M12 5v14M5 12h14"/>',
		external: '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
		alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
	};

	const esc = (value) => frappe.utils.escape_html(value === null || value === undefined ? "" : String(value));
	const bold = (value) => `<b>${esc(value)}</b>`;
	const icon = (name, size = 16) =>
		`<svg class="mcpc-i" width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${PATHS[name]}</svg>`;
	const clone = (value) => JSON.parse(JSON.stringify(value));
	const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
	const fmt_int = (n) => (n === null || n === undefined ? "—" : Number(n).toLocaleString());
	const when = (ts) => (ts ? frappe.datetime.prettyDate(ts) || frappe.datetime.str_to_user(ts) : "");
	const reduced_motion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
	const set_html = (el, markup) => {
		el.innerHTML = markup; // markup is assembled from esc()-ed values only
	};

	function switch_html({ checked, label, act, attrs = "", disabled = false, hide_label = false }) {
		return `<button type="button" class="mcpc-switch" role="switch" aria-checked="${checked ? "true" : "false"}"
			data-act="${act}" ${attrs} ${disabled ? "disabled" : ""}>
			<span class="mcpc-switch__track" aria-hidden="true"><span class="mcpc-switch__thumb"></span></span>
			<span class="mcpc-switch__label ${hide_label ? "sr-only" : ""}">${esc(label)}</span>
		</button>`;
	}

	function status_pill(status) {
		return `<span class="mcpc-pill mcpc-pill--${STATUS_CLASS[status] || "muted"}">${esc(__(status || "—"))}</span>`;
	}

	function segmented(name, options, value, label) {
		return `<div class="mcpc-seg" role="radiogroup" aria-label="${esc(label)}">${options
			.map(
				([val, text]) =>
					`<button type="button" role="radio" aria-checked="${val === value}" class="mcpc-seg__opt" data-act="seg" data-seg="${esc(name)}" data-value="${esc(val)}">${esc(text)}</button>`
			)
			.join("")}</div>`;
	}

	mcp_connector.Console = class Console {
		constructor(page, wrapper) {
			this.page = page;
			this.wrapper = wrapper;
			this.data = null;
			this.section = null;
			this.editor = null; // {profile, base, tab, preview}
			this.settings_draft = null;
			this.activity = { status: "", user: "", tool: "", hours: 24, rows: [], has_more: false };
			this.dash_hours = 24;
			this.connect = { user: "", keys: null, client: "claude_code", test: null };
			this.field_cache = {};
			this.report_cache = {};
			this.build_shell();
			this.bind_events();
			window.addEventListener("beforeunload", (e) => {
				if (this.is_dirty() || this.settings_dirty()) {
					e.preventDefault();
					e.returnValue = "";
				}
			});
			frappe.router.on("change", () => {
				if (frappe.get_route()[0] === "mcp-console" && this.data) this.on_show();
			});
		}

		// -- infrastructure ------------------------------------------------------
		call(method, args = {}, freeze = false) {
			return new Promise((resolve, reject) => {
				frappe.call({
					method: API + method,
					args,
					freeze,
					callback: (r) => resolve(r.message),
					error: (err) => reject(err || new Error(method)),
				});
			});
		}

		copy(text, what) {
			frappe.utils.copy_to_clipboard(text);
			if (what) frappe.show_alert({ message: __("{0} copied", [esc(what)]), indicator: "green" }, 3);
		}

		build_shell() {
			this.root = document.createElement("div");
			this.root.className = "mcpc mcpc-app";
			set_html(
				this.root,
				`<nav class="mcpc-rail" aria-label="${esc(__("MCP Console sections"))}">
					<div class="mcpc-rail__nav" role="list"></div>
					<div class="mcpc-rail__foot"></div>
				</nav>
				<main class="mcpc-main" tabindex="-1">
					<div class="mcpc-loading">${esc(__("Loading…"))}</div>
				</main>`
			);
			this.page.main.get(0).appendChild(this.root);
			this.$nav = this.root.querySelector(".mcpc-rail__nav");
			this.$foot = this.root.querySelector(".mcpc-rail__foot");
			this.$main = this.root.querySelector(".mcpc-main");
		}

		bind_events() {
			this.root.addEventListener("click", (e) => {
				const el = e.target.closest("[data-act]");
				if (!el || !this.root.contains(el) || el.disabled) return;
				this.handle(el.dataset.act, el, e);
			});
			this.root.addEventListener("input", (e) => {
				if (e.target.dataset && e.target.dataset.role === "matrix-filter") {
					this.editor.matrix_filter = e.target.value;
					const term = e.target.value.trim().toLowerCase();
					this.$main.querySelectorAll(".mcpc-matrix tbody tr[data-dt]").forEach((tr) => (tr.hidden = !!term && !tr.dataset.dt.includes(term)));
					return;
				}
				const el = e.target.closest("[data-bind]");
				if (el && this.root.contains(el)) this.bind_input(el);
			});
			this.root.addEventListener("keydown", (e) => {
				const tab = e.target.closest('[role="tab"]');
				if (tab && (e.key === "ArrowRight" || e.key === "ArrowLeft")) {
					const tabs = [...tab.parentElement.querySelectorAll('[role="tab"]')];
					const rtl = getComputedStyle(this.root).direction === "rtl";
					const forward = (e.key === "ArrowRight") !== rtl;
					const next = tabs[(tabs.indexOf(tab) + (forward ? 1 : tabs.length - 1)) % tabs.length];
					next.click();
					const again = this.root.querySelector(`[role="tab"][data-tab="${next.dataset.tab}"]`);
					if (again) again.focus();
					e.preventDefault();
				}
			});
			// Ctrl/Cmd+S saves wherever focus is. Capture phase on document, and the event is
			// stopped, so Frappe's global "primary action" shortcut cannot also fire (it would
			// click the save button of whatever form was opened last).
			document.addEventListener(
				"keydown",
				(e) => {
					if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "s") return;
					if (frappe.get_route()[0] !== "mcp-console" || (window.cur_dialog && window.cur_dialog.display)) return;
					let save = null;
					if (this.section === "profiles" && this.editor && (this.is_dirty() || !this.editor.profile.name)) save = () => this.save_profile();
					else if (this.section === "settings" && this.settings_dirty()) save = () => this.save_settings();
					if (!save) return;
					e.preventDefault();
					e.stopPropagation();
					save();
				},
				true
			);
		}

		// -- routing -------------------------------------------------------------
		async on_show() {
			if (!this.data) await this.load();
			const [, section, ...rest] = frappe.get_route();
			const target = SECTIONS.some(([key]) => key === section) ? section : "overview";
			this.show(target, rest.join("/") || null);
		}

		go(section, arg) {
			const leaving_editor = !(section === "profiles" && this.editor && arg === this.editor.profile.name);
			if (this.is_dirty() && leaving_editor) {
				frappe.confirm(__("Discard unsaved changes to this profile?"), () => {
					this.editor = null;
					this.go(section, arg);
				});
				return;
			}
			if (section !== "settings") this.settings_draft = null;
			const route = ["mcp-console", section];
			if (arg) route.push(arg);
			frappe.set_route(route);
		}

		async load() {
			try {
				this.data = await this.call("bootstrap");
			} catch (e) {
				set_html(
					this.$main,
					`<div class="mcpc-empty"><p>${esc(__("The MCP Console could not load. You need the System Manager or MCP Manager role."))}</p></div>`
				);
				throw e;
			}
			this.render_status();
		}

		render_status() {
			const on = this.data.settings.enabled;
			this.page.set_indicator(on ? __("Live") : __("Paused"), on ? "green" : "orange");
		}

		show(section, arg) {
			this.section = section;
			this.render_nav();
			this.root.dataset.section = section;
			({
				overview: () => this.render_overview(),
				profiles: () => this.render_profiles(arg),
				actions: () => this.render_actions(),
				inspector: () => this.render_inspector(arg),
				activity: () => this.render_activity(),
				connect: () => this.render_connect(),
				settings: () => this.render_settings(),
			})[section]();
		}

		render_nav() {
			const counts = {
				profiles: this.data.profiles.length,
				actions: this.data.actions.filter((a) => a.enabled).length,
			};
			set_html(
				this.$nav,
				SECTIONS.map(
					([key, label]) => `
				<button type="button" role="listitem" class="mcpc-nav ${key === this.section ? "is-active" : ""}"
					data-act="nav" data-section="${key}" ${key === this.section ? 'aria-current="page"' : ""}>
					<span>${esc(label)}</span>
					${counts[key] !== undefined ? `<span class="mcpc-nav__count">${counts[key]}</span>` : ""}
				</button>`
				).join("")
			);
			set_html(
				this.$foot,
				`<span class="mcpc-overline">${esc(__("Endpoint"))}</span>
				<button type="button" class="mcpc-linkbtn" data-act="copy-endpoint">${icon("copy", 14)}${esc(__("Copy URL"))}</button>`
			);
		}

		// -- event router --------------------------------------------------------
		handle(act, el, e) {
			const row = () => Number(el.dataset.row);
			const handlers = {
				nav: () => this.go(el.dataset.section),
				"copy-endpoint": () => this.copy(this.data.endpoint, __("Endpoint URL")),
				"copy-text": () => this.copy(el.dataset.text, el.dataset.what || __("Text")),
				seg: () => this.on_segment(el.dataset.seg, el.dataset.value),
				"toggle-server": () => this.toggle_server(),
				goto: () => this.go(el.dataset.section, el.dataset.arg),
				"new-profile": () => this.new_profile_dialog(),
				"open-profile": () => this.go("profiles", el.dataset.name),
				"editor-tab": () => this.set_editor_tab(el.dataset.tab),
				"toggle-enabled": () => this.edit((p) => (p.enabled = !p.enabled), "header"),
				"remove-chip": () => this.edit((p) => (p[el.dataset.list] = p[el.dataset.list].filter((v) => v !== el.dataset.value)), "access"),
				"toggle-type": () => this.toggle_type(el.dataset.type),
				"toggle-action": () => this.toggle_action(el.dataset.action),
				"type-all": () => this.select_type_actions(el.dataset.type),
				preset: () => this.apply_preset(el.dataset.preset),
				"toggle-op": () => this.toggle_op(row(), el.dataset.op),
				"toggle-column": () => this.toggle_column(el.dataset.op),
				"remove-doctype": () => this.edit((p) => p.doctypes.splice(row(), 1), "doctypes"),
				"bulk-doctypes": () => this.bulk_add_doctypes(),
				"bulk-reports": () => this.bulk_add_reports(),
				"clear-doctypes": () =>
					frappe.confirm(__("Remove all {0} DocTypes from this profile?", [this.editor.profile.doctypes.length]), () =>
						this.edit((p) => (p.doctypes = []), "doctypes")
					),
				"edit-fields": () => this.fields_dialog(row()),
				"edit-scope": () => this.scope_dialog(row()),
				"remove-report": () => this.edit((p) => p.reports.splice(row(), 1), "reports"),
				"add-fixed": () => this.edit((p) => p.reports[row()].fixed_rows.push({ key: "", value: "" }), "reports"),
				"remove-fixed": () => this.edit((p) => p.reports[row()].fixed_rows.splice(Number(el.dataset.idx), 1), "reports"),
				"save-profile": () => this.save_profile(),
				"discard-profile": () => this.discard_profile(),
				"delete-profile": () => this.delete_profile(),
				"duplicate-profile": () => this.duplicate_profile(),
				"toggle-action-enabled": () => this.toggle_action_enabled(el.dataset.name),
				"edit-action": () => this.action_dialog(this.data.actions.find((a) => a.name === el.dataset.name)),
				"new-action": () => this.action_dialog(null),
				"open-log": () => this.log_dialog(el.dataset.name),
				"more-logs": () => this.load_logs(false),
				"generate-keys": () => this.generate_keys(),
				"run-test": () => this.run_test(),
				"toggle-setting": () => this.toggle_setting(el.dataset.field),
				"save-settings": () => this.save_settings(),
				"discard-settings": () => {
					this.settings_draft = null;
					this.render_settings();
				},
			};
			if (handlers[act]) {
				e.preventDefault();
				handlers[act]();
			}
		}

		on_segment(name, value) {
			if (name === "dash-hours") {
				this.dash_hours = Number(value);
				this.load_dashboard();
			} else if (name === "log-status") {
				this.activity.status = value;
				this.load_logs(true);
			} else if (name === "client") {
				this.connect.client = value;
				this.render_connect_snippet();
			} else if (name === "audit-mode") {
				this.settings_draft.audit_mode = value;
				this.render_settings();
				return;
			}
			this.root
				.querySelectorAll(`[data-seg="${CSS.escape(name)}"]`)
				.forEach((b) => b.setAttribute("aria-checked", String(b.dataset.value === value)));
		}

		bind_input(el) {
			const [scope, key, index, sub] = el.dataset.bind.split(".");
			const value = el.type === "number" ? (el.value === "" ? "" : Number(el.value)) : el.value;
			if (scope === "profile" && this.editor) {
				const p = this.editor.profile;
				if (key === "report-notes") p.reports[Number(index)].notes = value;
				else if (key === "fixed") {
					const [idx, part] = sub.split(":");
					p.reports[Number(index)].fixed_rows[Number(idx)][part] = value;
				} else p[key] = value;
				this.update_savebar();
			} else if (scope === "settings" && this.settings_draft) {
				this.settings_draft[key] = value;
				this.update_settings_savebar();
			} else if (scope === "profile-search") {
				this.render_profile_list(value);
			}
		}

		// ===================================================================== overview
		render_overview() {
			const s = this.data.settings;
			const d = this.data.dashboard;
			const has_profiles = this.data.profiles.length > 0;
			set_html(
				this.$main,
				`<section class="mcpc-hero" aria-labelledby="mcpc-hero-title">
					<div class="mcpc-hero__copy">
						<p class="mcpc-overline">${esc(__("MCP server"))}</p>
						<h1 class="mcpc-display" id="mcpc-hero-title">
							<span class="mcpc-dot mcpc-dot--${s.enabled ? "ok" : "warn"}" aria-hidden="true"></span>${esc(s.enabled ? __("Live") : __("Paused"))}
						</h1>
						<p class="mcpc-lede">${esc(
							s.enabled
								? __("AI clients connect with an ERPNext API key. Every call runs as that user and is checked against your access profiles.")
								: __("New MCP requests are refused until you resume the server. Profiles and logs are kept.")
						)}</p>
						<div class="mcpc-endpoint">
							<code>${esc(this.data.endpoint)}</code>
							<button type="button" class="btn btn-default btn-sm" data-act="copy-endpoint">${icon("copy", 14)}<span>${esc(__("Copy"))}</span></button>
						</div>
					</div>
					<div class="mcpc-hero__aside">
						${switch_html({ checked: s.enabled, label: s.enabled ? __("Accepting connections") : __("Paused"), act: "toggle-server" })}
					</div>
				</section>
				${
					has_profiles && d.total
						? ""
						: `<section class="mcpc-setup" aria-labelledby="mcpc-setup-title">
					<h2 class="mcpc-h2" id="mcpc-setup-title">${esc(__("Get connected in three steps"))}</h2>
					<ol class="mcpc-steps">
						<li class="${has_profiles ? "is-done" : ""}"><strong>${esc(__("Create an access profile"))}</strong>
							<span>${esc(__("Choose the roles, actions, DocTypes and reports the AI may use."))}</span>
							<button type="button" class="mcpc-linkbtn" data-act="goto" data-section="profiles">${esc(has_profiles ? __("Review profiles") : __("Create a profile"))}</button></li>
						<li><strong>${esc(__("Connect a client"))}</strong>
							<span>${esc(__("Generate an API key and paste the configuration into Claude, Cursor or VS Code."))}</span>
							<button type="button" class="mcpc-linkbtn" data-act="goto" data-section="connect">${esc(__("Open setup"))}</button></li>
						<li class="${d.total ? "is-done" : ""}"><strong>${esc(__("Watch the activity"))}</strong>
							<span>${esc(__("Every tool call lands here and in the MCP Audit Log."))}</span></li>
					</ol>
				</section>`
				}
				<section class="mcpc-block" aria-labelledby="mcpc-activity-title">
					<header class="mcpc-block__head">
						<h2 class="mcpc-h2" id="mcpc-activity-title">${esc(__("Activity"))}</h2>
						${segmented("dash-hours", [["24", __("24 hours")], ["168", __("7 days")], ["720", __("30 days")]], String(this.dash_hours), __("Time range"))}
					</header>
					<div class="mcpc-dash"></div>
				</section>`
			);
			this.render_dashboard();
		}

		async load_dashboard() {
			this.data.dashboard = await this.call("dashboard", { hours: this.dash_hours });
			if (this.section === "overview") this.render_dashboard();
		}

		render_dashboard() {
			const d = this.data.dashboard;
			const $dash = this.$main.querySelector(".mcpc-dash");
			if (!$dash) return;
			const range = { 24: __("24 hours"), 168: __("7 days"), 720: __("30 days") }[d.hours];
			const bar_list = (items, cls) => {
				if (!items.length) return `<p class="mcpc-muted">${esc(__("Nothing yet."))}</p>`;
				const max = items[0][1];
				return `<ol class="mcpc-bars">${items
					.map(
						([name, n]) => `<li><span class="mcpc-bars__name ${cls}">${esc(name)}</span><span class="mcpc-bars__n">${fmt_int(n)}</span>
						<span class="mcpc-bars__track" aria-hidden="true"><span style="width:${Math.max(4, (n * 100) / max).toFixed(1)}%"></span></span></li>`
					)
					.join("")}</ol>`;
			};
			set_html(
				$dash,
				`<dl class="mcpc-kpis">
					<div><dt>${esc(__("Calls"))}</dt><dd>${fmt_int(d.total)}</dd><span>${esc(range)}</span></div>
					<div><dt>${esc(__("Success rate"))}</dt><dd>${d.success_rate === null ? "—" : esc(d.success_rate) + "<small>%</small>"}</dd><span>${esc(plural(d.success, __("call"), __("calls")))}</span></div>
					<div><dt>${esc(__("Denied"))}</dt><dd class="${d.denied ? "is-warn" : ""}">${fmt_int(d.denied)}</dd><span>${esc(plural(d.errors, __("error"), __("errors")))}</span></div>
					<div><dt>${esc(__("Median latency"))}</dt><dd>${d.median_ms === null ? "—" : esc(d.median_ms) + "<small>ms</small>"}</dd><span>${esc(d.p95_ms === null ? "" : __("p95 {0} ms", [d.p95_ms]))}</span></div>
				</dl>
				<div class="mcpc-chart" role="img" aria-label="${esc(__("Calls per period by result: success, denied and error"))}"></div>
				<div class="mcpc-split">
					<div><h3 class="mcpc-h3">${esc(__("Most used actions"))}</h3>${bar_list(d.top_tools, "mcpc-mono")}</div>
					<div><h3 class="mcpc-h3">${esc(__("Most active users"))}</h3>${bar_list(d.top_users, "")}</div>
				</div>
				<div class="mcpc-recent">
					<header class="mcpc-block__head"><h3 class="mcpc-h3">${esc(__("Latest calls"))}</h3>
						<button type="button" class="mcpc-linkbtn" data-act="goto" data-section="activity">${esc(__("All activity"))}</button></header>
					${this.log_table(d.recent, true)}
				</div>`
			);
			this.draw_chart($dash.querySelector(".mcpc-chart"), d);
		}

		draw_chart(el, d) {
			if (!d.total) {
				set_html(el, `<div class="mcpc-chart__empty">${esc(__("No calls in this period yet."))}</div>`);
				return;
			}
			const hourly = d.hours <= 48;
			const labels = d.series.labels.map((l) => (hourly ? l.slice(11) + ":00" : frappe.datetime.str_to_user(l)));
			try {
				new frappe.Chart(el, {
					data: {
						labels,
						datasets: [
							{ name: __("Success"), values: d.series.Success },
							{ name: __("Denied"), values: d.series.Denied },
							{ name: __("Error"), values: d.series.Error },
						],
					},
					type: "bar",
					height: 220,
					colors: CHART_COLORS,
					barOptions: { stacked: 1, spaceRatio: 0.4 },
					axisOptions: { xIsSeries: 1, xAxisMode: "tick", yAxisMode: "span" },
					animate: reduced_motion() ? 0 : 1,
				});
			} catch (err) {
				set_html(el, `<div class="mcpc-chart__empty">${esc(__("Chart unavailable"))}</div>`);
			}
		}

		async toggle_server() {
			const s = clone(this.data.settings);
			const apply = async () => {
				s.enabled = !s.enabled;
				this.data.settings = await this.call("save_settings", { settings: s });
				this.settings_draft = null;
				this.render_status();
				frappe.show_alert({ message: s.enabled ? __("MCP server resumed") : __("MCP server paused"), indicator: s.enabled ? "green" : "orange" }, 4);
				this.render_overview();
			};
			if (s.enabled) frappe.confirm(__("Pause the MCP server? Connected AI clients get “403 disabled” until you resume it."), apply);
			else await apply();
		}

		// ===================================================================== profiles
		render_profiles(name) {
			this.render_profiles_layout();
			if (name && this.editor && this.editor.profile.name === name) this.render_editor();
			else if (name) this.open_profile(name);
			else if (this.editor && !this.editor.profile.name) this.render_editor();
			else {
				this.editor = null;
				this.render_editor();
			}
		}

		render_profiles_layout() {
			const count = this.data.profiles.length;
			set_html(
				this.$main,
				`<header class="mcpc-head">
					<div>
						<h1 class="mcpc-title">${esc(__("Access profiles"))}</h1>
						<p class="mcpc-lede">${esc(__("A profile decides what an AI client may do: for which roles or users, with which action types and actions, on which DocTypes and reports. Anything no profile grants is denied."))}</p>
					</div>
					<button type="button" class="btn btn-primary btn-sm" data-act="new-profile">${icon("plus", 14)}<span>${esc(__("New profile"))}</span></button>
				</header>
				<div class="mcpc-profiles ${count ? "" : "is-empty"}">
					<aside class="mcpc-plist" aria-label="${esc(__("Profiles"))}">
						${count > 6 ? `<input type="search" class="form-control mcpc-plist__search" placeholder="${esc(__("Filter profiles"))}" aria-label="${esc(__("Filter profiles"))}" data-bind="profile-search">` : ""}
						<div class="mcpc-plist__items"></div>
					</aside>
					<div class="mcpc-editor"></div>
				</div>`
			);
			this.render_profile_list("");
		}

		render_profile_list(filter) {
			const $items = this.$main.querySelector(".mcpc-plist__items");
			if (!$items) return;
			const term = (filter || "").toLowerCase();
			const current = this.editor && this.editor.profile.name;
			const rows = this.data.profiles.filter((p) => !term || p.name.toLowerCase().includes(term));
			set_html(
				$items,
				rows
					.map((p) => {
						const who =
							[
								p.roles.length ? plural(p.roles.length, __("role"), __("roles")) : "",
								p.users.length ? plural(p.users.length, __("user"), __("users")) : "",
							]
								.filter(Boolean)
								.join(" · ") || __("Nobody assigned");
						const what = [plural(p.doctypes, __("DocType"), __("DocTypes")), plural(p.reports, __("report"), __("reports"))].join(" · ");
						return `<button type="button" class="mcpc-pitem ${p.name === current ? "is-active" : ""}" data-act="open-profile" data-name="${esc(p.name)}" ${p.name === current ? 'aria-current="true"' : ""}>
							<span class="mcpc-pitem__top"><span class="mcpc-pitem__name">${esc(p.name)}</span>
							${p.enabled ? "" : `<span class="mcpc-pill mcpc-pill--muted">${esc(__("Disabled"))}</span>`}</span>
							<span class="mcpc-pitem__meta">${esc(who)}</span>
							<span class="mcpc-pitem__meta">${esc(what)}</span>
						</button>`;
					})
					.join("") || `<p class="mcpc-muted mcpc-pad">${esc(term ? __("No profile matches.") : __("No profiles yet."))}</p>`
			);
		}

		async open_profile(name) {
			const $editor = this.$main.querySelector(".mcpc-editor");
			if ($editor) set_html($editor, `<div class="mcpc-loading">${esc(__("Loading profile…"))}</div>`);
			try {
				const profile = await this.call("get_profile", { name });
				this.editor = { profile: this.to_working(profile), base: null, tab: (this.editor && this.editor.tab) || "access", preview: null };
				this.editor.base = this.snapshot();
			} catch (e) {
				this.editor = null;
			}
			if (this.section === "profiles") {
				this.render_profile_list("");
				this.render_editor();
			}
		}

		to_working(profile) {
			const p = clone(profile);
			p.reports = p.reports.map((r) => ({
				...r,
				fixed_rows: Object.entries(r.fixed_filters || {}).map(([key, value]) => ({ key, value: Array.isArray(value) ? value.join(", ") : String(value) })),
			}));
			return p;
		}

		payload() {
			const p = this.editor.profile;
			return {
				original_name: p.name || "",
				profile_name: (p.profile_name || "").trim(),
				enabled: !!p.enabled,
				description: p.description || "",
				roles: p.roles,
				users: p.users,
				action_types: TYPES.filter((t) => p.action_types.includes(t)),
				actions: p.actions,
				doctypes: p.doctypes.map((d) => ({
					doctype: d.doctype,
					operations: OPS.filter((op) => d.operations.includes(op)),
					field_mode: d.field_mode,
					field_list: d.field_mode === "All Fields" ? [] : d.field_list,
					scope: d.scope && (Array.isArray(d.scope) ? d.scope.length : Object.keys(d.scope).length) ? d.scope : null,
					notes: d.notes || "",
				})),
				reports: p.reports.map((r) => {
					const fixed = {};
					r.fixed_rows.filter((f) => f.key.trim()).forEach((f) => (fixed[f.key.trim()] = f.value));
					return { report: r.report, fixed_filters: Object.keys(fixed).length ? fixed : null, notes: r.notes || "" };
				}),
			};
		}

		snapshot() {
			return JSON.stringify(this.payload());
		}

		is_dirty() {
			return !!(this.editor && this.editor.base !== null && this.snapshot() !== this.editor.base);
		}

		edit(mutator, part) {
			mutator(this.editor.profile);
			this.editor.preview = null;
			this.render_editor(part);
		}

		new_profile_dialog() {
			const d = new frappe.ui.Dialog({
				title: __("New access profile"),
				fields: [
					{ fieldname: "profile_name", fieldtype: "Data", label: __("Name"), reqd: 1, description: __("For example: Sales assistant, Finance read-only") },
					{
						fieldname: "preset",
						fieldtype: "Select",
						label: __("Start from"),
						options: [
							{ value: "read", label: __("Read-only assistant — look things up and run reports") },
							{ value: "write", label: __("Operations assistant — read, create and update") },
							{ value: "blank", label: __("Blank — nothing allowed yet") },
						],
						default: "read",
					},
				],
				primary_action_label: __("Create"),
				primary_action: (values) => {
					const name = (values.profile_name || "").trim();
					if (this.data.profiles.some((p) => p.name.toLowerCase() === name.toLowerCase())) {
						frappe.msgprint(__("A profile named {0} already exists", [bold(name)]));
						return;
					}
					d.hide();
					this.editor = {
						profile: {
							name: "",
							profile_name: name,
							enabled: true,
							description: "",
							roles: [],
							users: [],
							action_types: [],
							actions: [],
							doctypes: [],
							reports: [],
							matched_users: 0,
						},
						base: null,
						tab: "access",
						preview: null,
					};
					this.apply_preset(values.preset, true);
					this.editor.base = "{}";
					if (frappe.get_route()[1] !== "profiles" || frappe.get_route()[2]) frappe.set_route("mcp-console", "profiles");
					else this.render_profiles(null);
				},
			});
			d.show();
		}

		render_editor(part) {
			const $editor = this.$main.querySelector(".mcpc-editor");
			if (!$editor) return;
			if (!this.editor) {
				set_html(
					$editor,
					this.data.profiles.length
						? `<div class="mcpc-empty"><p class="mcpc-empty__title">${esc(__("Select a profile"))}</p><p>${esc(__("Choose a profile to edit who it applies to and what it allows."))}</p></div>`
						: `<div class="mcpc-empty"><p class="mcpc-empty__title">${esc(__("No access profiles yet"))}</p>
						<p>${esc(__("Until you create one, every MCP request is refused. Start with a read-only profile and widen it as you gain confidence."))}</p>
						<button type="button" class="btn btn-primary btn-sm" data-act="new-profile">${icon("plus", 14)}<span>${esc(__("Create the first profile"))}</span></button></div>`
				);
				return;
			}
			if (part && $editor.querySelector(".mcpc-ed__body")) {
				this.render_editor_part(part);
				if (part !== "header") this.render_editor_part("tabs");
				this.update_savebar();
				return;
			}
			const p = this.editor.profile;
			set_html(
				$editor,
				`<div class="mcpc-ed">
					<header class="mcpc-ed__head">
						<div class="mcpc-ed__titlebox">
							<label class="sr-only" for="mcpc-profile-name">${esc(__("Profile name"))}</label>
							<input id="mcpc-profile-name" class="mcpc-ed__name" data-bind="profile.profile_name" value="${esc(p.profile_name)}" maxlength="140" autocomplete="off" spellcheck="false">
							<div class="mcpc-ed__status"></div>
						</div>
						<div class="mcpc-ed__tools">
							${
								p.name
									? `<button type="button" class="btn btn-default btn-xs" data-act="duplicate-profile">${esc(__("Duplicate"))}</button>
							<button type="button" class="btn btn-default btn-xs mcpc-danger" data-act="delete-profile">${esc(__("Delete"))}</button>`
									: `<span class="mcpc-pill mcpc-pill--info">${esc(__("Not saved yet"))}</span>`
							}
						</div>
					</header>
					<label class="sr-only" for="mcpc-profile-desc">${esc(__("Description"))}</label>
					<textarea id="mcpc-profile-desc" class="mcpc-ed__desc" rows="1" placeholder="${esc(__("What is this profile for? (optional)"))}" data-bind="profile.description" maxlength="2000">${esc(p.description)}</textarea>
					<div class="mcpc-tabs" role="tablist" aria-label="${esc(__("Profile sections"))}"></div>
					<div class="mcpc-ed__body" role="tabpanel" tabindex="0"></div>
					<div class="mcpc-savebar" role="region" aria-label="${esc(__("Unsaved changes"))}" hidden>
						<span class="mcpc-savebar__msg">${esc(p.name ? __("Unsaved changes") : __("New profile — not saved yet"))}</span>
						<kbd class="mcpc-savebar__hint">Ctrl S</kbd>
						<button type="button" class="btn btn-default btn-sm" data-act="discard-profile">${esc(__("Discard"))}</button>
						<button type="button" class="btn btn-primary btn-sm" data-act="save-profile">${esc(p.name ? __("Save profile") : __("Create profile"))}</button>
					</div>
				</div>`
			);
			const $desc = $editor.querySelector(".mcpc-ed__desc");
			const grow = () => {
				$desc.style.height = "auto";
				$desc.style.height = $desc.scrollHeight + "px";
			};
			$desc.addEventListener("input", grow);
			requestAnimationFrame(grow);
			this.render_editor_part("header");
			this.render_editor_part("tabs");
			this.render_editor_part("body");
			this.update_savebar();
		}

		render_editor_part(part) {
			const $editor = this.$main.querySelector(".mcpc-editor");
			const p = this.editor.profile;
			if (part === "header") {
				set_html(
					$editor.querySelector(".mcpc-ed__status"),
					switch_html({ checked: p.enabled, label: p.enabled ? __("Enabled") : __("Disabled"), act: "toggle-enabled" })
				);
				return;
			}
			if (part === "tabs") {
				const tabs = [
					["access", __("Who"), p.roles.length + p.users.length],
					["capabilities", __("Capabilities"), p.actions.length],
					["doctypes", __("DocTypes"), p.doctypes.length],
					["reports", __("Reports"), p.reports.length],
					["preview", __("AI view"), null],
				];
				set_html(
					$editor.querySelector(".mcpc-tabs"),
					tabs
						.map(
							([key, label, n]) => `<button type="button" role="tab" id="mcpc-tab-${key}" aria-selected="${key === this.editor.tab}" tabindex="${key === this.editor.tab ? 0 : -1}"
							class="mcpc-tab" data-act="editor-tab" data-tab="${key}">${esc(label)}${n === null ? "" : `<span class="mcpc-tab__n">${n}</span>`}</button>`
						)
						.join("")
				);
				return;
			}
			if (part !== "body" && part !== this.editor.tab) return;
			const $body = $editor.querySelector(".mcpc-ed__body");
			$body.setAttribute("aria-labelledby", `mcpc-tab-${this.editor.tab}`);
			({
				access: () => this.render_access($body),
				capabilities: () => this.render_capabilities($body),
				doctypes: () => this.render_doctypes($body),
				reports: () => this.render_reports($body),
				preview: () => this.render_preview($body),
			})[this.editor.tab]();
		}

		set_editor_tab(tab) {
			this.editor.tab = tab;
			this.render_editor_part("tabs");
			this.render_editor_part("body");
		}

		update_savebar() {
			const $bar = this.$main.querySelector(".mcpc-editor .mcpc-savebar");
			if (!$bar) return;
			$bar.hidden = !(this.is_dirty() || (this.editor && !this.editor.profile.name));
		}

		make_adder(parent, doctype, placeholder, on_pick, { filters = null, get_query = null } = {}) {
			const control = frappe.ui.form.make_control({
				parent,
				df: {
					fieldtype: "Link",
					options: doctype,
					fieldname: `mcpc_add_${doctype.toLowerCase().replace(/\s+/g, "_")}`,
					placeholder,
					label: placeholder,
					only_select: true,
					get_query: get_query || (filters ? () => ({ filters }) : null),
					change: () => {
						const value = control.get_value();
						if (!value) return;
						control.set_value("");
						on_pick(value);
					},
				},
				render_input: true,
			});
			control.$wrapper.find(".control-label").addClass("sr-only");
			control.$input.attr("aria-label", placeholder);
			return control;
		}

		// -- "Who" tab
		render_access($body) {
			const p = this.editor.profile;
			const chips = (list, values, empty) =>
				values.length
					? `<ul class="mcpc-chips">${values
							.map(
								(v) => `<li class="mcpc-chip"><span>${esc(v)}</span><button type="button" class="mcpc-chip__x" data-act="remove-chip" data-list="${list}" data-value="${esc(v)}" aria-label="${esc(__("Remove {0}", [v]))}">${icon("x", 12)}</button></li>`
							)
							.join("")}</ul>`
					: `<p class="mcpc-muted">${esc(empty)}</p>`;
			set_html(
				$body,
				`<div class="mcpc-form-grid">
					<section>
						<h3 class="mcpc-h3">${esc(__("Roles"))}</h3>
						<p class="mcpc-help">${esc(__("Everyone holding one of these roles gets this profile."))}</p>
						${chips("roles", p.roles, __("No roles yet."))}
						<div class="mcpc-adder" data-adder="roles"></div>
					</section>
					<section>
						<h3 class="mcpc-h3">${esc(__("Users"))}</h3>
						<p class="mcpc-help">${esc(__("Grant it directly, for example to a dedicated integration user."))}</p>
						${chips("users", p.users, __("No users yet."))}
						<div class="mcpc-adder" data-adder="users"></div>
					</section>
				</div>
				<p class="mcpc-note">${esc(
					p.name
						? __("Applies to {0} right now.", [plural(p.matched_users || 0, __("enabled user"), __("enabled users"))])
						: __("Save the profile to see how many users it applies to.")
				)}</p>`
			);
			this.make_adder($body.querySelector('[data-adder="roles"]'), "Role", __("Add a role"), (value) => {
				if (!p.roles.includes(value)) this.edit((pp) => pp.roles.push(value), "access");
			});
			this.make_adder(
				$body.querySelector('[data-adder="users"]'),
				"User",
				__("Add a user"),
				(value) => {
					if (!p.users.includes(value)) this.edit((pp) => pp.users.push(value), "access");
				},
				{ filters: { enabled: 1, name: ["!=", "Guest"] } }
			);
		}

		// -- capabilities tab
		actions_by_type() {
			const groups = {};
			TYPES.forEach((t) => (groups[t] = []));
			this.data.actions.forEach((a) => (groups[a.action_type] || (groups[a.action_type] = [])).push(a));
			return groups;
		}

		render_capabilities($body) {
			const p = this.editor.profile;
			const groups = this.actions_by_type();
			set_html(
				$body,
				`<div class="mcpc-presets">
					<span class="mcpc-help">${esc(__("Quick start:"))}</span>
					<button type="button" class="btn btn-default btn-xs" data-act="preset" data-preset="read">${esc(__("Read only"))}</button>
					<button type="button" class="btn btn-default btn-xs" data-act="preset" data-preset="write">${esc(__("Read & write"))}</button>
					<button type="button" class="btn btn-default btn-xs" data-act="preset" data-preset="all">${esc(__("Everything"))}</button>
					<button type="button" class="btn btn-default btn-xs" data-act="preset" data-preset="blank">${esc(__("Nothing"))}</button>
				</div>
				<p class="mcpc-help">${esc(__("Each action type is a master switch. When it is off, its actions and the matching DocType operations stay inactive in this profile."))}</p>
				<div class="mcpc-types">
					${TYPES.map((type) => {
						const on = p.action_types.includes(type);
						const actions = groups[type] || [];
						const selected = actions.filter((a) => p.actions.includes(a.name)).length;
						return `<section class="mcpc-type ${on ? "is-on" : ""}" aria-label="${esc(TYPE_INFO[type][0])}">
							<div class="mcpc-type__head">
								${switch_html({ checked: on, label: TYPE_INFO[type][0], act: "toggle-type", attrs: `data-type="${type}"` })}
								<span class="mcpc-type__desc">${esc(TYPE_INFO[type][1])}</span>
								<span class="mcpc-type__count">${actions.length ? esc(__("{0} of {1}", [selected, actions.length])) : ""}
								${actions.length && selected < actions.length ? `<button type="button" class="mcpc-linkbtn" data-act="type-all" data-type="${type}">${esc(__("Select all"))}</button>` : ""}</span>
							</div>
							${
								actions.length
									? `<div class="mcpc-acts">${actions
											.map((a) => {
												const pressed = p.actions.includes(a.name);
												return `<button type="button" class="mcpc-act ${a.enabled ? "" : "is-off"}" aria-pressed="${pressed}" data-act="toggle-action" data-action="${esc(a.name)}"
												title="${esc(a.description || a.default_description || a.title)}">
												<span class="mcpc-act__box" aria-hidden="true">${pressed ? icon("check", 12) : ""}</span>
												<span class="mcpc-mono">${esc(a.name)}</span>
												${a.is_standard ? "" : `<span class="mcpc-pill mcpc-pill--info">${esc(__("custom"))}</span>`}
												${a.enabled ? "" : `<span class="mcpc-pill mcpc-pill--muted">${esc(__("globally off"))}</span>`}
											</button>`;
											})
											.join("")}</div>`
									: type === "Custom"
									? `<p class="mcpc-muted mcpc-type__empty">${esc(__("No custom actions defined."))} <button type="button" class="mcpc-linkbtn" data-act="goto" data-section="actions">${esc(__("Create one"))}</button></p>`
									: ""
							}
							${on || !selected ? "" : `<p class="mcpc-warnline">${icon("alert", 14)}${esc(__("Selected actions stay inactive until {0} is switched on.", [TYPE_INFO[type][0]]))}</p>`}
						</section>`;
					}).join("")}
				</div>`
			);
		}

		toggle_type(type) {
			this.edit((p) => {
				if (p.action_types.includes(type)) p.action_types = p.action_types.filter((t) => t !== type);
				else {
					p.action_types.push(type);
					// Switching a type on with none of its actions picked selects them all: the usual intent.
					const names = (this.actions_by_type()[type] || []).filter((a) => a.enabled).map((a) => a.name);
					if (!names.some((a) => p.actions.includes(a))) p.actions.push(...names);
				}
			}, "capabilities");
		}

		toggle_action(name) {
			this.edit((p) => {
				p.actions = p.actions.includes(name) ? p.actions.filter((a) => a !== name) : [...p.actions, name];
			}, "capabilities");
		}

		select_type_actions(type) {
			this.edit((p) => (this.actions_by_type()[type] || []).forEach((a) => p.actions.includes(a.name) || p.actions.push(a.name)), "capabilities");
		}

		apply_preset(preset, silent) {
			const types = { read: ["Read", "Report"], write: ["Read", "Create", "Update", "Report"], all: TYPES, blank: [] }[preset] || [];
			const mutate = (p) => {
				p.action_types = [...types];
				p.actions = this.data.actions.filter((a) => a.enabled && a.is_standard && types.includes(a.action_type)).map((a) => a.name);
			};
			if (silent) mutate(this.editor.profile);
			else this.edit(mutate, "capabilities");
		}

		// -- doctypes tab
		op_state(row, op) {
			const meta = row.meta || {};
			if ((op === "Submit" || op === "Cancel") && meta.exists && !meta.is_submittable) return "na";
			if (op !== "Read" && meta.exists && meta.writable === false) return "locked";
			const checked = row.operations.includes(op);
			if (checked && !this.editor.profile.action_types.includes(op)) return "inactive";
			return checked ? "on" : "off";
		}

		render_doctypes($body) {
			const p = this.editor.profile;
			const field_label = (d) =>
				d.field_mode === "All Fields"
					? __("All fields")
					: d.field_mode === "Only Listed Fields"
					? __("Only {0}", [d.field_list.length])
					: __("Hides {0}", [d.field_list.length]);
			const scope_count = (d) => (d.scope ? (Array.isArray(d.scope) ? d.scope.length : Object.keys(d.scope).length) : 0);
			const cell = (d, i, op) => {
				const state = this.op_state(d, op);
				if (state === "na") return `<td class="mcpc-cell is-na"><span class="sr-only">${esc(__("Not submittable"))}</span><span aria-hidden="true">—</span></td>`;
				if (state === "locked")
					return `<td class="mcpc-cell is-locked" title="${esc(__("{0} is read-only over MCP", [d.doctype]))}">${icon("lock", 14)}<span class="sr-only">${esc(__("Locked"))}</span></td>`;
				const checked = state === "on" || state === "inactive";
				return `<td class="mcpc-cell"><button type="button" role="checkbox" aria-checked="${checked}" aria-label="${esc(__("{0} on {1}", [__(op), d.doctype]))}"
					class="mcpc-check ${state === "inactive" ? "is-inactive" : ""}" data-act="toggle-op" data-row="${i}" data-op="${op}"
					${state === "inactive" ? `title="${esc(__("{0} is switched off in Capabilities", [__(op)]))}"` : ""}>${checked ? icon("check", 13) : ""}</button></td>`;
			};
			set_html(
				$body,
				`<div class="mcpc-toolbar">
					<p class="mcpc-help">${esc(__("Grant operations per DocType. ERPNext role and user permissions still apply on top of these rules."))}</p>
					<div class="mcpc-toolbar__add">
						<div class="mcpc-adder mcpc-adder--wide" data-adder="doctype"></div>
						<button type="button" class="btn btn-default btn-sm" data-act="bulk-doctypes">${icon("plus", 14)}<span>${esc(__("Add several"))}</span></button>
					</div>
				</div>
				${
					p.doctypes.length > 1
						? `<div class="mcpc-matrixbar">
					${
						p.doctypes.length > 6
							? `<input type="search" class="form-control" data-role="matrix-filter" value="${esc(this.editor.matrix_filter || "")}" placeholder="${esc(__("Filter DocTypes"))}" aria-label="${esc(__("Filter DocTypes"))}">`
							: ""
					}
					<span class="mcpc-help mcpc-matrixbar__count">${esc(plural(p.doctypes.length, __("DocType"), __("DocTypes")))}</span>
					<button type="button" class="mcpc-linkbtn mcpc-linkbtn--danger" data-act="clear-doctypes">${esc(__("Remove all"))}</button>
				</div>`
						: ""
				}
				${
					p.doctypes.length
						? `<div class="mcpc-tablewrap"><table class="mcpc-matrix">
					<caption class="sr-only">${esc(__("DocType permissions"))}</caption>
					<thead><tr>
						<th scope="col" class="mcpc-matrix__dt">${esc(__("DocType"))}</th>
						${OPS.map(
							(op) => `<th scope="col" class="mcpc-matrix__op ${p.action_types.includes(op) ? "" : "is-inactive"}">
								<button type="button" class="mcpc-colbtn" data-act="toggle-column" data-op="${op}" title="${esc(__("Toggle {0} for every DocType", [__(op)]))}">${esc(__(op))}</button></th>`
						).join("")}
						<th scope="col"><span class="sr-only">${esc(__("Remove"))}</span></th>
					</tr></thead>
					<tbody>
						${p.doctypes
							.map((d, i) => {
								const meta = d.meta || {};
								const badges = [
									meta.exists === false ? `<span class="mcpc-pill mcpc-pill--bad">${esc(__("missing"))}</span>` : "",
									meta.is_submittable ? `<span class="mcpc-pill mcpc-pill--muted">${esc(__("submittable"))}</span>` : "",
									meta.issingle ? `<span class="mcpc-pill mcpc-pill--muted">${esc(__("single"))}</span>` : "",
									meta.writable === false ? `<span class="mcpc-pill mcpc-pill--warn">${esc(__("read-only"))}</span>` : "",
								].join("");
								const n = scope_count(d);
								const term = (this.editor.matrix_filter || "").trim().toLowerCase();
								return `<tr data-dt="${esc(d.doctype.toLowerCase())}" ${term && !d.doctype.toLowerCase().includes(term) ? "hidden" : ""}>
									<th scope="row" class="mcpc-matrix__dt"><span class="mcpc-matrix__name">${esc(d.doctype)}</span>
										<span class="mcpc-matrix__meta">${esc(meta.module || "")} ${badges}</span>
										<span class="mcpc-matrix__cfg">
											<button type="button" class="mcpc-cellbtn ${d.field_mode === "All Fields" ? "" : "is-set"}" data-act="edit-fields" data-row="${i}" aria-label="${esc(__("Fields of {0}: {1}", [d.doctype, field_label(d)]))}">${esc(field_label(d))}</button>
											<button type="button" class="mcpc-cellbtn ${n || d.notes ? "is-set" : ""}" data-act="edit-scope" data-row="${i}" aria-label="${esc(__("Scope and notes of {0}", [d.doctype]))}" ${meta.issingle ? "disabled" : ""}>${esc(
												n ? plural(n, __("condition"), __("conditions")) : __("All records")
											)}${d.notes ? ` · ${esc(__("notes"))}` : ""}</button>
										</span></th>
									${OPS.map((op) => cell(d, i, op)).join("")}
									<td><button type="button" class="mcpc-iconbtn" data-act="remove-doctype" data-row="${i}" aria-label="${esc(__("Remove {0}", [d.doctype]))}">${icon("x", 14)}</button></td>
								</tr>`;
							})
							.join("")}
					</tbody></table></div>
					<p class="mcpc-legend"><span>${icon("check", 12)} ${esc(__("granted"))}</span><span><span class="mcpc-check is-inactive mcpc-legend__box" aria-hidden="true">${icon("check", 10)}</span>${esc(__("granted, but the action type is off"))}</span><span>${icon("lock", 12)} ${esc(__("read-only over MCP"))}</span><span>— ${esc(__("not submittable"))}</span></p>`
						: `<div class="mcpc-empty mcpc-empty--inline"><p class="mcpc-empty__title">${esc(__("No DocTypes yet"))}</p><p>${esc(__("Add the record types this profile may reach, such as Customer, Item or Sales Invoice."))}</p></div>`
				}`
			);
			const existing = new Set(p.doctypes.map((d) => d.doctype));
			this.make_adder(
				$body.querySelector('[data-adder="doctype"]'),
				"DocType",
				__("Add a DocType"),
				async (value) => {
					if (existing.has(value)) return frappe.show_alert({ message: __("{0} is already listed", [esc(value)]), indicator: "orange" });
					const info = await this.doctype_info(value);
					if (!info.meta.exposable) return frappe.msgprint(__("{0} can never be exposed over MCP.", [bold(value)]));
					if (info.meta.istable) return frappe.msgprint(__("{0} is a child table; add its parent DocType instead.", [bold(value)]));
					this.edit((pp) => pp.doctypes.push({ doctype: value, operations: ["Read"], field_mode: "All Fields", field_list: [], scope: null, notes: "", meta: info.meta }), "doctypes");
				},
				{ get_query: () => ({ filters: [["DocType", "istable", "=", 0], ["DocType", "name", "not in", this.data.protected.never]] }) }
			);
		}

		async catalog(kind) {
			this.catalogs = this.catalogs || {};
			if (!this.catalogs[kind]) this.catalogs[kind] = await this.call(kind === "doctypes" ? "doctype_catalog" : "report_catalog", {}, true);
			return this.catalogs[kind];
		}

		// A searchable, module-grouped multi-select. items: [{name, module, ...}]
		bulk_picker({ title, noun, items, taken, badges, grant, on_apply }) {
			const state = { term: "", selected: new Set(), ops: new Set(["Read"]) };
			const d = new frappe.ui.Dialog({
				title: esc(title),
				size: "extra-large",
				fields: [{ fieldtype: "HTML", fieldname: "body" }],
				primary_action_label: __("Add"),
				primary_action: () => {
					if (!state.selected.size) return frappe.msgprint(__("Tick at least one {0}.", [esc(noun[0])]));
					if (grant && !state.ops.size) return frappe.msgprint(__("Choose at least one operation to grant."));
					on_apply([...state.selected], state);
					d.hide();
				},
			});
			const $body = d.fields_dict.body.$wrapper.get(0);
			$body.classList.add("mcpc", "mcpc-dialog", "mcpc-bulk");
			const visible = () => {
				const term = state.term.trim().toLowerCase();
				return items.filter((it) => !term || it.name.toLowerCase().includes(term) || (it.module || "").toLowerCase().includes(term));
			};
			const groups = () => {
				const map = new Map();
				visible().forEach((it) => {
					const key = it.module || __("Other");
					if (!map.has(key)) map.set(key, []);
					map.get(key).push(it);
				});
				return map;
			};
			const update_footer = () => {
				const n = state.selected.size;
				d.get_primary_btn().text(n ? __("Add {0}", [plural(n, noun[0], noun[1])]) : __("Add"));
				const $count = $body.querySelector(".mcpc-bulk__count");
				if ($count) $count.textContent = __("{0} selected", [n]);
			};
			const list_html = () => {
				const g = groups();
				if (!g.size) return `<p class="mcpc-muted mcpc-pad">${esc(__("Nothing matches “{0}”.", [state.term]))}</p>`;
				return [...g.entries()]
					.map(([module, rows]) => {
						const free = rows.filter((r) => !taken.has(r.name));
						const all_on = free.length && free.every((r) => state.selected.has(r.name));
						return `<section class="mcpc-bgroup">
							<header class="mcpc-bgroup__head">
								<span class="mcpc-bgroup__name">${esc(module)}</span>
								<span class="mcpc-bgroup__count">${rows.length}</span>
								${free.length ? `<button type="button" class="mcpc-linkbtn" data-k="group" data-group="${esc(module)}">${esc(all_on ? __("Clear") : __("Select all {0}", [free.length]))}</button>` : ""}
							</header>
							<div class="mcpc-bgrid">${rows
								.map((r) => {
									const is_taken = taken.has(r.name);
									const on = is_taken || state.selected.has(r.name);
									return `<button type="button" role="checkbox" aria-checked="${on}" class="mcpc-field mcpc-bitem" data-k="item" data-name="${esc(r.name)}" ${is_taken ? "disabled" : ""}>
										<span class="mcpc-act__box" aria-hidden="true">${on ? icon("check", 12) : ""}</span>
										<span class="mcpc-field__label">${esc(r.name)}</span>
										<span class="mcpc-bitem__badges">${is_taken ? `<span class="mcpc-pill mcpc-pill--muted">${esc(__("added"))}</span>` : badges(r)}</span>
									</button>`;
								})
								.join("")}</div>
						</section>`;
					})
					.join("");
			};
			const render_list = () => {
				set_html($body.querySelector(".mcpc-bulk__list"), list_html());
				update_footer();
			};
			set_html(
				$body,
				`<div class="mcpc-bulk__bar">
					<input type="search" class="form-control" data-k="search" placeholder="${esc(__("Search by name or module"))}" aria-label="${esc(__("Search"))}">
					<span class="mcpc-help mcpc-bulk__count"></span>
					<button type="button" class="mcpc-linkbtn" data-k="shown">${esc(__("Select everything shown"))}</button>
					<button type="button" class="mcpc-linkbtn" data-k="none">${esc(__("Clear selection"))}</button>
				</div>
				${
					grant
						? `<div class="mcpc-bulk__grant"><span class="mcpc-label">${esc(__("Grant to everything added"))}</span>
					<div class="mcpc-acts">${OPS.map(
						(op) =>
							`<button type="button" class="mcpc-act" aria-pressed="${state.ops.has(op)}" data-k="op" data-op="${op}"><span class="mcpc-act__box" aria-hidden="true">${
								state.ops.has(op) ? icon("check", 12) : ""
							}</span>${esc(__(op))}</button>`
					).join("")}</div>
					<p class="mcpc-help">${esc(
						__("Submit and Cancel are applied only to submittable DocTypes, and write operations are skipped where a DocType is read-only over MCP. You can fine-tune each row afterwards.")
					)}</p></div>`
						: ""
				}
				<div class="mcpc-bulk__list" role="group" aria-label="${esc(title)}"></div>`
			);
			let timer = null;
			$body.addEventListener("input", (e) => {
				if (e.target.dataset.k !== "search") return;
				state.term = e.target.value;
				clearTimeout(timer);
				timer = setTimeout(render_list, 120);
			});
			$body.addEventListener("click", (e) => {
				const el = e.target.closest("[data-k]");
				if (!el || el.disabled) return;
				const k = el.dataset.k;
				if (k === "item") {
					const name = el.dataset.name;
					state.selected.has(name) ? state.selected.delete(name) : state.selected.add(name);
				} else if (k === "group") {
					const rows = (groups().get(el.dataset.group) || []).filter((r) => !taken.has(r.name));
					const all_on = rows.every((r) => state.selected.has(r.name));
					rows.forEach((r) => (all_on ? state.selected.delete(r.name) : state.selected.add(r.name)));
				} else if (k === "shown") visible().forEach((r) => taken.has(r.name) || state.selected.add(r.name));
				else if (k === "none") state.selected.clear();
				else if (k === "op") {
					const op = el.dataset.op;
					state.ops.has(op) ? state.ops.delete(op) : state.ops.add(op);
					el.setAttribute("aria-pressed", String(state.ops.has(op)));
					set_html(el.querySelector(".mcpc-act__box"), state.ops.has(op) ? icon("check", 12) : "");
					return;
				} else return;
				render_list();
			});
			render_list();
			d.show();
			setTimeout(() => $body.querySelector('[data-k="search"]').focus(), 200);
		}

		async bulk_add_doctypes() {
			const items = await this.catalog("doctypes");
			const by_name = new Map(items.map((it) => [it.name, it]));
			this.bulk_picker({
				title: __("Add DocTypes"),
				noun: [__("DocType"), __("DocTypes")],
				items,
				taken: new Set(this.editor.profile.doctypes.map((d) => d.doctype)),
				grant: true,
				badges: (r) =>
					[
						r.is_submittable ? `<span class="mcpc-pill mcpc-pill--muted">${esc(__("submittable"))}</span>` : "",
						r.issingle ? `<span class="mcpc-pill mcpc-pill--muted">${esc(__("single"))}</span>` : "",
						r.writable ? "" : `<span class="mcpc-pill mcpc-pill--warn">${esc(__("read-only"))}</span>`,
						r.custom ? `<span class="mcpc-pill mcpc-pill--info">${esc(__("custom"))}</span>` : "",
					].join(""),
				on_apply: (names, state) => {
					this.edit((p) => {
						for (const name of names) {
							const it = by_name.get(name);
							let ops = OPS.filter(
								(op) => state.ops.has(op) && (!["Submit", "Cancel"].includes(op) || it.is_submittable) && (op === "Read" || it.writable)
							);
							if (!ops.length) ops = ["Read"];
							p.doctypes.push({
								doctype: name,
								operations: ops,
								field_mode: "All Fields",
								field_list: [],
								scope: null,
								notes: "",
								meta: { exists: true, module: it.module, is_submittable: it.is_submittable, issingle: it.issingle, istable: false, exposable: true, writable: it.writable },
							});
						}
					}, "doctypes");
					frappe.show_alert({ message: __("Added {0}", [esc(plural(names.length, __("DocType"), __("DocTypes")))]), indicator: "green" }, 3);
				},
			});
		}

		async bulk_add_reports() {
			const items = await this.catalog("reports");
			const by_name = new Map(items.map((it) => [it.name, it]));
			this.bulk_picker({
				title: __("Add reports"),
				noun: [__("report"), __("reports")],
				items,
				taken: new Set(this.editor.profile.reports.map((r) => r.report)),
				grant: false,
				badges: (r) => `<span class="mcpc-pill mcpc-pill--muted">${esc(r.report_type)}</span>`,
				on_apply: (names) => {
					this.edit((p) => {
						for (const name of names) {
							const it = by_name.get(name);
							p.reports.push({ report: name, fixed_filters: null, fixed_rows: [], notes: "", meta: { exists: true, report_type: it.report_type, ref_doctype: it.ref_doctype } });
						}
					}, "reports");
					frappe.show_alert({ message: __("Added {0}", [esc(plural(names.length, __("report"), __("reports")))]), indicator: "green" }, 3);
				},
			});
		}

		toggle_op(i, op) {
			this.edit((p) => {
				const d = p.doctypes[i];
				d.operations = d.operations.includes(op) ? d.operations.filter((o) => o !== op) : [...d.operations, op];
			}, "doctypes");
		}

		toggle_column(op) {
			this.edit((p) => {
				const eligible = p.doctypes.filter((d) => ["on", "off", "inactive"].includes(this.op_state(d, op)));
				const all_on = eligible.length && eligible.every((d) => d.operations.includes(op));
				eligible.forEach((d) => {
					if (all_on) d.operations = d.operations.filter((o) => o !== op);
					else if (!d.operations.includes(op)) d.operations = [...d.operations, op];
				});
			}, "doctypes");
		}

		async doctype_info(doctype) {
			if (!this.field_cache[doctype]) this.field_cache[doctype] = await this.call("describe_doctype", { doctype });
			return this.field_cache[doctype];
		}

		async fields_dialog(i) {
			const row = this.editor.profile.doctypes[i];
			const info = await this.doctype_info(row.doctype);
			const state = { mode: row.field_mode, selected: new Set(row.field_list), term: "" };
			const d = new frappe.ui.Dialog({
				title: __("Fields · {0}", [esc(row.doctype)]),
				size: "large",
				fields: [{ fieldtype: "HTML", fieldname: "body" }],
				primary_action_label: __("Apply"),
				primary_action: () => {
					if (state.mode === "Only Listed Fields" && !state.selected.size) return frappe.msgprint(__("Pick at least one field, or choose All fields."));
					this.edit((p) => {
						p.doctypes[i].field_mode = state.mode;
						p.doctypes[i].field_list = state.mode === "All Fields" ? [] : [...state.selected].sort();
					}, "doctypes");
					d.hide();
				},
			});
			const $body = d.fields_dict.body.$wrapper.get(0);
			$body.classList.add("mcpc", "mcpc-dialog");
			const list_html = () => {
				const fields = info.fields.filter((f) => !state.term || `${f.label} ${f.fieldname}`.toLowerCase().includes(state.term));
				return `<div class="mcpc-fieldlist" role="group" aria-label="${esc(__("Fields"))}">${
					fields
						.map((f) => {
							const on = state.selected.has(f.fieldname);
							return `<button type="button" role="checkbox" aria-checked="${on}" class="mcpc-field" data-k="pick" data-field="${esc(f.fieldname)}" ${state.mode === "All Fields" ? "disabled" : ""}>
							<span class="mcpc-act__box" aria-hidden="true">${on ? icon("check", 12) : ""}</span>
							<span class="mcpc-field__label">${esc(f.label)}</span>
							<span class="mcpc-mono mcpc-field__name">${esc(f.fieldname)}</span>
							<span class="mcpc-field__type">${esc(f.is_table ? __("table") : f.fieldtype)}</span></button>`;
						})
						.join("") || `<p class="mcpc-muted">${esc(__("No field matches."))}</p>`
				}</div>`;
			};
			const render = () => {
				const explain = {
					"All Fields": __("The AI can read and write every field its ERPNext role allows. Password fields are never exposed."),
					"Only Listed Fields": __("The AI sees and may set only the fields you tick. Name, status and timestamps stay visible."),
					"Exclude Listed Fields": __("The AI sees everything except the fields you tick, and cannot filter or sort on them."),
				}[state.mode];
				set_html(
					$body,
					`${segmented("field-mode", [["All Fields", __("All fields")], ["Only Listed Fields", __("Only selected")], ["Exclude Listed Fields", __("All except selected")]], state.mode, __("Field access"))}
					<p class="mcpc-help">${esc(explain)}</p>
					<div class="mcpc-fieldbar">
						<input type="search" class="form-control" data-k="search" placeholder="${esc(__("Search fields"))}" aria-label="${esc(__("Search fields"))}" value="${esc(state.term)}" ${state.mode === "All Fields" ? "disabled" : ""}>
						<span class="mcpc-help">${esc(plural(state.selected.size, __("field selected"), __("fields selected")))}</span>
						${state.selected.size ? `<button type="button" class="mcpc-linkbtn" data-k="clear">${esc(__("Clear"))}</button>` : ""}
					</div>
					${list_html()}`
				);
			};
			$body.addEventListener("click", (e) => {
				const el = e.target.closest("[data-k], [data-seg]");
				if (!el || el.disabled) return;
				if (el.dataset.seg) state.mode = el.dataset.value;
				else if (el.dataset.k === "pick") {
					const f = el.dataset.field;
					state.selected.has(f) ? state.selected.delete(f) : state.selected.add(f);
				} else if (el.dataset.k === "clear") state.selected.clear();
				else return;
				render();
				// Keep keyboard users in place; mouse users need no focus ring.
				const again = el.dataset.field && e.detail === 0 ? $body.querySelector(`[data-field="${CSS.escape(el.dataset.field)}"]`) : null;
				if (again) again.focus();
			});
			$body.addEventListener("input", (e) => {
				if (e.target.dataset.k !== "search") return;
				state.term = e.target.value.toLowerCase();
				$body.querySelector(".mcpc-fieldlist").outerHTML = list_html();
			});
			render();
			d.show();
		}

		async scope_dialog(i) {
			const row = this.editor.profile.doctypes[i];
			const info = await this.doctype_info(row.doctype);
			const fields = info.fields.filter((f) => !f.is_table);
			const rows = [];
			const scope = row.scope;
			if (Array.isArray(scope))
				scope.forEach((c) => rows.push(c.length === 4 ? { field: c[1], op: c[2], value: c[3] } : { field: c[0], op: c[1], value: c[2] }));
			else if (scope && typeof scope === "object")
				Object.entries(scope).forEach(([field, v]) =>
					rows.push(
						Array.isArray(v) && v.length === 2 && OPERATORS.includes(String(v[0]).toLowerCase())
							? { field, op: String(v[0]).toLowerCase(), value: v[1] }
							: { field, op: Array.isArray(v) ? "in" : "=", value: v }
					)
				);
			const state = { rows, notes: row.notes || "" };
			const value_text = (v) => (Array.isArray(v) ? v.join(", ") : v === null || v === undefined ? "" : String(v));
			const field_options = (sel) =>
				`<option value="">${esc(__("Field…"))}</option>` +
				fields.map((f) => `<option value="${esc(f.fieldname)}" ${f.fieldname === sel ? "selected" : ""}>${esc(f.label)} (${esc(f.fieldname)})</option>`).join("");
			const d = new frappe.ui.Dialog({
				title: __("Scope & notes · {0}", [esc(row.doctype)]),
				size: "large",
				fields: [{ fieldtype: "HTML", fieldname: "body" }],
				primary_action_label: __("Apply"),
				primary_action: () => {
					const conditions = [];
					for (const r of state.rows) {
						if (!r.field) continue;
						const f = fields.find((x) => x.fieldname === r.field) || {};
						const to_value = (x) => (NUMERIC.has(f.fieldtype) && x !== "" && !isNaN(Number(x)) ? Number(x) : x);
						let value;
						if (["in", "not in", "between"].includes(r.op))
							value = value_text(r.value)
								.split(",")
								.map((x) => to_value(x.trim()))
								.filter((x) => x !== "");
						else if (r.op === "is") value = r.value === "not set" ? "not set" : "set";
						else value = to_value(value_text(r.value));
						if (r.op === "between" && value.length !== 2) return frappe.msgprint(__("“between” needs two values separated by a comma."));
						if (["in", "not in"].includes(r.op) && !value.length) return frappe.msgprint(__("“{0}” needs at least one value.", [esc(r.op)]));
						conditions.push([r.field, r.op, value]);
					}
					this.edit((p) => {
						p.doctypes[i].scope = conditions.length ? conditions : null;
						p.doctypes[i].notes = state.notes.trim();
					}, "doctypes");
					d.hide();
				},
			});
			const $body = d.fields_dict.body.$wrapper.get(0);
			$body.classList.add("mcpc", "mcpc-dialog");
			const render = () => {
				set_html(
					$body,
					`<p class="mcpc-help">${esc(__("The AI can only reach records matching every condition — when listing, reading or changing them — and new or edited records must still match. No conditions means every record."))}</p>
					<div class="mcpc-conds">
						${
							state.rows
								.map(
									(r, idx) => `<div class="mcpc-cond" data-idx="${idx}">
								<select class="form-control" data-k="field" aria-label="${esc(__("Field"))}">${field_options(r.field)}</select>
								<select class="form-control" data-k="op" aria-label="${esc(__("Operator"))}">${OPERATORS.map((op) => `<option ${op === r.op ? "selected" : ""}>${esc(op)}</option>`).join("")}</select>
								${
									r.op === "is"
										? `<select class="form-control" data-k="value" aria-label="${esc(__("Value"))}"><option value="set" ${r.value !== "not set" ? "selected" : ""}>${esc(__("set"))}</option><option value="not set" ${r.value === "not set" ? "selected" : ""}>${esc(__("not set"))}</option></select>`
										: `<input class="form-control" data-k="value" aria-label="${esc(__("Value"))}" value="${esc(value_text(r.value))}" placeholder="${esc(["in", "not in"].includes(r.op) ? __("a, b, c") : r.op === "between" ? __("from, to") : __("value"))}">`
								}
								<button type="button" class="mcpc-iconbtn" data-k="remove" aria-label="${esc(__("Remove condition"))}">${icon("x", 14)}</button>
							</div>`
								)
								.join("") || `<p class="mcpc-muted">${esc(__("No conditions: every record is in scope."))}</p>`
						}
					</div>
					<button type="button" class="mcpc-linkbtn" data-k="add">${icon("plus", 14)}${esc(__("Add condition"))}</button>
					<label class="mcpc-label" for="mcpc-scope-notes">${esc(__("Notes for the AI"))}</label>
					<textarea id="mcpc-scope-notes" class="form-control" rows="2" data-k="notes" maxlength="1000" placeholder="${esc(__("Shown to the AI with this DocType, e.g. “B2B customers only; always set territory”."))}">${esc(state.notes)}</textarea>`
				);
			};
			$body.addEventListener("click", (e) => {
				const el = e.target.closest("[data-k]");
				if (!el) return;
				if (el.dataset.k === "add") {
					state.rows.push({ field: "", op: "=", value: "" });
					render();
					const selects = $body.querySelectorAll('[data-k="field"]');
					if (selects.length) selects[selects.length - 1].focus();
				} else if (el.dataset.k === "remove") {
					state.rows.splice(Number(el.closest(".mcpc-cond").dataset.idx), 1);
					render();
				}
			});
			const on_change = (e) => {
				const el = e.target.closest("[data-k]");
				if (!el) return;
				if (el.dataset.k === "notes") {
					state.notes = el.value;
					return;
				}
				const cond = el.closest(".mcpc-cond");
				if (!cond) return;
				const r = state.rows[Number(cond.dataset.idx)];
				r[el.dataset.k] = el.value;
				if (el.dataset.k === "op" && e.type === "change") {
					if (el.value === "is") r.value = "set";
					render();
				}
			};
			$body.addEventListener("input", on_change);
			$body.addEventListener("change", on_change);
			render();
			d.show();
		}

		// -- reports tab
		render_reports($body) {
			const p = this.editor.profile;
			set_html(
				$body,
				`<div class="mcpc-toolbar">
					<p class="mcpc-help">${esc(__("Reports the AI may run. Fixed filters are forced on every run, whatever the AI asks for — use them to pin a company or cost center."))}</p>
					<div class="mcpc-toolbar__add">
						<div class="mcpc-adder mcpc-adder--wide" data-adder="report"></div>
						<button type="button" class="btn btn-default btn-sm" data-act="bulk-reports">${icon("plus", 14)}<span>${esc(__("Add several"))}</span></button>
					</div>
				</div>
				${
					p.reports.length
						? `<ul class="mcpc-reports">${p.reports
								.map((r, i) => {
									const meta = r.meta || {};
									return `<li class="mcpc-report">
									<div class="mcpc-report__head">
										<div><span class="mcpc-report__name">${esc(r.report)}</span>
										<span class="mcpc-matrix__meta">${esc(meta.report_type || "")}${meta.ref_doctype ? " · " + esc(meta.ref_doctype) : ""}
										${meta.exists === false ? `<span class="mcpc-pill mcpc-pill--bad">${esc(__("missing"))}</span>` : ""}
										${meta.disabled ? `<span class="mcpc-pill mcpc-pill--warn">${esc(__("disabled"))}</span>` : ""}</span></div>
										<button type="button" class="mcpc-iconbtn" data-act="remove-report" data-row="${i}" aria-label="${esc(__("Remove {0}", [r.report]))}">${icon("x", 14)}</button>
									</div>
									<div class="mcpc-report__body">
										<div>
											<span class="mcpc-label">${esc(__("Fixed filters"))}</span>
											${r.fixed_rows
												.map(
													(f, idx) => `<div class="mcpc-kv">
												<input class="form-control mcpc-mono" list="mcpc-rf-${i}" aria-label="${esc(__("Filter"))}" placeholder="${esc(__("filter, e.g. company"))}" value="${esc(f.key)}" data-bind="profile.fixed.${i}.${idx}:key">
												<input class="form-control" aria-label="${esc(__("Value"))}" placeholder="${esc(__("value"))}" value="${esc(f.value)}" data-bind="profile.fixed.${i}.${idx}:value">
												<button type="button" class="mcpc-iconbtn" data-act="remove-fixed" data-row="${i}" data-idx="${idx}" aria-label="${esc(__("Remove filter"))}">${icon("x", 14)}</button></div>`
												)
												.join("")}
											<datalist id="mcpc-rf-${i}"></datalist>
											<button type="button" class="mcpc-linkbtn" data-act="add-fixed" data-row="${i}">${icon("plus", 14)}${esc(__("Add fixed filter"))}</button>
										</div>
										<div>
											<label class="mcpc-label" for="mcpc-rn-${i}">${esc(__("Notes for the AI"))}</label>
											<input id="mcpc-rn-${i}" class="form-control" value="${esc(r.notes)}" maxlength="1000" data-bind="profile.report-notes.${i}" placeholder="${esc(__("e.g. Always pass from_date and to_date"))}">
										</div>
									</div>
								</li>`;
								})
								.join("")}</ul>`
						: `<div class="mcpc-empty mcpc-empty--inline"><p class="mcpc-empty__title">${esc(__("No reports yet"))}</p><p>${esc(__("Add reports such as General Ledger or Stock Balance, and switch on the Reports action type."))}</p></div>`
				}`
			);
			p.reports.forEach((r, i) => this.fill_report_filters(r.report, $body.querySelector(`#mcpc-rf-${i}`)));
			const existing = new Set(p.reports.map((r) => r.report));
			this.make_adder(
				$body.querySelector('[data-adder="report"]'),
				"Report",
				__("Add a report"),
				async (value) => {
					if (existing.has(value)) return frappe.show_alert({ message: __("{0} is already listed", [esc(value)]), indicator: "orange" });
					const info = await this.report_info(value);
					this.edit(
						(pp) => pp.reports.push({ report: value, fixed_filters: null, fixed_rows: [], notes: "", meta: { exists: true, report_type: info.report_type, ref_doctype: info.ref_doctype } }),
						"reports"
					);
				},
				{ filters: { disabled: 0 } }
			);
		}

		async report_info(report) {
			if (!this.report_cache[report]) this.report_cache[report] = await this.call("describe_report", { report });
			return this.report_cache[report];
		}

		async fill_report_filters(report, datalist) {
			try {
				const info = await this.report_info(report);
				if (datalist) set_html(datalist, (info.filters || []).map((f) => `<option value="${esc(f.fieldname)}">${esc(f.label || "")}</option>`).join(""));
			} catch (e) {
				// The report no longer exists: nothing to suggest.
			}
		}

		// -- AI view tab
		async render_preview($body) {
			const p = this.editor.profile;
			if (!p.name) {
				set_html($body, `<div class="mcpc-empty mcpc-empty--inline"><p>${esc(__("Create the profile to preview what an AI client will see."))}</p></div>`);
				return;
			}
			if (!this.editor.preview) {
				set_html($body, `<div class="mcpc-loading">${esc(__("Building the AI view…"))}</div>`);
				this.editor.preview = await this.call("preview_profile", { name: p.name });
				if (!this.editor || this.editor.tab !== "preview") return;
			}
			const tools = this.editor.preview.tools;
			set_html(
				$body,
				`<p class="mcpc-help">${esc(__("The tools an AI client receives from this profile alone, exactly as sent over MCP. ERPNext role permissions can narrow the DocType lists further; the Inspector shows that for a specific user."))}</p>
				${this.is_dirty() ? `<p class="mcpc-warnline">${icon("alert", 14)}${esc(__("You have unsaved changes; the preview shows the saved profile."))}</p>` : ""}
				${
					tools.length
						? this.tool_list(tools)
						: `<div class="mcpc-empty mcpc-empty--inline"><p class="mcpc-empty__title">${esc(__("No tools exposed"))}</p><p>${esc(__("Switch on an action type, pick its actions and grant at least one DocType or report."))}</p></div>`
				}`
			);
		}

		tool_list(tools) {
			return `<ul class="mcpc-tools">${tools
				.map(
					(t) => `<li class="mcpc-tool">
					<div class="mcpc-tool__head"><span class="mcpc-mono mcpc-tool__name">${esc(t.name)}</span>
						${t.action_type ? `<span class="mcpc-pill mcpc-pill--muted">${esc(__(t.action_type))}</span>` : ""}
						${t.read_only ? `<span class="mcpc-pill mcpc-pill--ok">${esc(__("read-only"))}</span>` : ""}
						${t.destructive ? `<span class="mcpc-pill mcpc-pill--bad">${esc(__("destructive"))}</span>` : ""}
						${t.is_standard ? "" : `<span class="mcpc-pill mcpc-pill--info">${esc(__("custom"))}</span>`}</div>
					<p class="mcpc-tool__desc">${esc(t.description)}</p>
					${t.doctypes ? `<p class="mcpc-tool__scope"><span>${esc(__("DocTypes"))}</span>${t.doctypes.map((x) => `<code>${esc(x)}</code>`).join("")}</p>` : ""}
					${t.reports ? `<p class="mcpc-tool__scope"><span>${esc(__("Reports"))}</span>${t.reports.map((x) => `<code>${esc(x)}</code>`).join("")}</p>` : ""}
					<details class="mcpc-details"><summary>${esc(__("Input schema"))}</summary><pre>${esc(JSON.stringify(t.input_schema, null, 2))}</pre></details>
				</li>`
				)
				.join("")}</ul>`;
		}

		// -- save / discard / delete / duplicate
		async save_profile() {
			const payload = this.payload();
			if (!payload.profile_name) {
				frappe.msgprint(__("Give the profile a name."));
				return;
			}
			const $btn = this.$main.querySelector('[data-act="save-profile"]');
			if ($btn) $btn.disabled = true;
			try {
				const saved = await this.call("save_profile", { profile: payload }, true);
				this.editor = { profile: this.to_working(saved), base: null, tab: this.editor.tab, preview: null };
				this.editor.base = this.snapshot();
				this.data.profiles = await this.call("profiles");
				frappe.show_alert({ message: __("Profile {0} saved", [esc(saved.name)]), indicator: "green" }, 4);
				this.render_nav();
				if (frappe.get_route()[2] !== saved.name) frappe.set_route("mcp-console", "profiles", saved.name);
				else this.render_profiles(saved.name);
			} catch (e) {
				if ($btn) $btn.disabled = false;
			}
		}

		discard_profile() {
			if (!this.editor.profile.name) {
				this.editor = null;
				this.render_profiles(null);
				return;
			}
			this.open_profile(this.editor.profile.name);
		}

		delete_profile() {
			const name = this.editor.profile.name;
			frappe.confirm(__("Delete the profile {0}? Users who relied on it lose MCP access immediately.", [bold(name)]), async () => {
				await this.call("delete_profile", { name }, true);
				this.editor = null;
				this.data.profiles = await this.call("profiles");
				frappe.show_alert({ message: __("Profile deleted"), indicator: "orange" }, 4);
				this.render_nav();
				frappe.set_route("mcp-console", "profiles");
			});
		}

		duplicate_profile() {
			const name = this.editor.profile.name;
			frappe.prompt(
				{ fieldname: "new_name", fieldtype: "Data", label: __("Name of the copy"), reqd: 1, default: __("{0} (copy)", [name]) },
				async (values) => {
					const created = await this.call("duplicate_profile", { name, new_name: values.new_name }, true);
					this.data.profiles = await this.call("profiles");
					frappe.show_alert({ message: __("Created {0} — disabled until you enable it", [esc(created)]), indicator: "green" }, 5);
					this.editor = null;
					this.render_nav();
					frappe.set_route("mcp-console", "profiles", created);
				},
				__("Duplicate profile"),
				__("Duplicate")
			);
		}

		// ===================================================================== actions
		render_actions() {
			const groups = this.actions_by_type();
			set_html(
				this.$main,
				`<header class="mcpc-head">
					<div>
						<h1 class="mcpc-title">${esc(__("Actions"))}</h1>
						<p class="mcpc-lede">${esc(__("The tools AI clients can call. Standard actions ship with the app; custom actions expose your own whitelisted Python methods or API Server Scripts. Switching an action off here hides it from every profile."))}</p>
					</div>
					<button type="button" class="btn btn-primary btn-sm" data-act="new-action">${icon("plus", 14)}<span>${esc(__("New custom action"))}</span></button>
				</header>
				${TYPES.filter((t) => (groups[t] || []).length)
					.map(
						(type) => `<section class="mcpc-agroup" aria-labelledby="mcpc-ag-${type}">
						<h2 class="mcpc-overline" id="mcpc-ag-${type}">${esc(TYPE_INFO[type][0])}</h2>
						<ul class="mcpc-alist">${groups[type]
							.map(
								(a) => `<li class="mcpc-arow ${a.enabled ? "" : "is-off"}">
								${switch_html({ checked: a.enabled, label: __("Enable {0}", [a.name]), act: "toggle-action-enabled", attrs: `data-name="${esc(a.name)}"`, hide_label: true })}
								<div class="mcpc-arow__main">
									<div class="mcpc-arow__title"><span class="mcpc-mono">${esc(a.name)}</span><span class="mcpc-arow__sub">${esc(a.title)}</span>
									${a.is_standard ? "" : `<span class="mcpc-pill mcpc-pill--info">${esc(__("custom"))}</span>`}
									${a.is_standard && a.description ? `<span class="mcpc-pill mcpc-pill--muted">${esc(__("edited text"))}</span>` : ""}</div>
									<p class="mcpc-arow__desc">${esc(a.description || a.default_description)}</p>
								</div>
								<span class="mcpc-arow__used">${esc(a.used_by.length ? plural(a.used_by.length, __("profile"), __("profiles")) : __("unused"))}</span>
								<button type="button" class="btn btn-default btn-xs" data-act="edit-action" data-name="${esc(a.name)}">${esc(__("Edit"))}</button>
							</li>`
							)
							.join("")}</ul>
					</section>`
					)
					.join("")}`
			);
		}

		async toggle_action_enabled(name) {
			const a = this.data.actions.find((x) => x.name === name);
			const res = await this.call("save_action", { action: this.action_payload(a, { enabled: !a.enabled }) });
			this.data.actions = res.actions;
			this.render_nav();
			this.render_actions();
			const again = this.$main.querySelector(`[data-act="toggle-action-enabled"][data-name="${CSS.escape(name)}"]`);
			if (again) again.focus();
		}

		action_payload(a, overrides = {}) {
			return {
				is_new: false,
				tool_name: a.name,
				title: a.title || "",
				action_type: a.action_type,
				enabled: !!a.enabled,
				description: a.description || "",
				method: a.method || "",
				input_schema: a.input_schema || null,
				read_only_hint: !!a.read_only_hint,
				destructive_hint: !!a.destructive_hint,
				...overrides,
			};
		}

		action_dialog(action) {
			const is_new = !action;
			const standard = action && action.is_standard;
			const example = JSON.stringify({ type: "object", properties: { item_code: { type: "string", description: "Item code" } }, required: ["item_code"] }, null, 2);
			const fields = standard
				? [
						{ fieldtype: "HTML", fieldname: "intro", options: `<p class="mcpc-help">${esc(__("Standard actions are implemented by the app. You can rewrite the description the AI reads, or switch the action off."))}</p>` },
						{ fieldname: "enabled", fieldtype: "Check", label: __("Enabled"), default: action.enabled ? 1 : 0 },
						{
							fieldname: "description",
							fieldtype: "Small Text",
							label: __("Description for the AI"),
							default: action.description || "",
							description: __("Leave empty to use the built-in text: {0}", [esc(action.default_description)]),
						},
				  ]
				: [
						{ fieldname: "tool_name", fieldtype: "Data", label: __("Tool name"), reqd: 1, read_only: is_new ? 0 : 1, default: action ? action.name : "", description: __("snake_case, e.g. get_stock_balance") },
						{ fieldname: "title", fieldtype: "Data", label: __("Title"), default: action ? action.title : "" },
						{ fieldname: "col1", fieldtype: "Column Break" },
						{ fieldname: "action_type", fieldtype: "Select", label: __("Action type"), options: TYPES.join("\n"), default: action ? action.action_type : "Custom", description: __("A profile must switch this type on to use the action.") },
						{ fieldname: "enabled", fieldtype: "Check", label: __("Enabled"), default: action ? (action.enabled ? 1 : 0) : 1 },
						{ fieldname: "sec1", fieldtype: "Section Break" },
						{ fieldname: "description", fieldtype: "Small Text", label: __("Description for the AI"), reqd: 1, default: action ? action.description : "", description: __("The AI decides when to call the action from this text. Say what it returns.") },
						{ fieldname: "method", fieldtype: "Data", label: __("Method"), reqd: 1, default: action ? action.method : "", description: __("Dotted path of a @frappe.whitelist() method, or the API method name of an API Server Script. It runs with the user's ERPNext permissions; MCP DocType rules do not apply inside it.") },
						{ fieldname: "input_schema", fieldtype: "Code", options: "JSON", label: __("Input schema (JSON Schema)"), default: action && action.input_schema ? JSON.stringify(action.input_schema, null, 2) : example },
						{ fieldname: "sec2", fieldtype: "Section Break" },
						{ fieldname: "read_only_hint", fieldtype: "Check", label: __("Read-only: has no side effects"), default: action && action.read_only_hint ? 1 : 0 },
						{ fieldname: "col2", fieldtype: "Column Break" },
						{ fieldname: "destructive_hint", fieldtype: "Check", label: __("Destructive: deletes or overwrites data"), default: action && action.destructive_hint ? 1 : 0 },
				  ];
			const d = new frappe.ui.Dialog({
				title: is_new ? __("New custom action") : standard ? __("Standard action · {0}", [esc(action.name)]) : __("Custom action · {0}", [esc(action.name)]),
				size: standard ? "" : "large",
				fields,
				primary_action_label: is_new ? __("Create action") : __("Save"),
				primary_action: async (values) => {
					let schema = null;
					if (!standard && (values.input_schema || "").trim()) {
						try {
							schema = JSON.parse(values.input_schema);
						} catch (err) {
							frappe.msgprint(__("Input schema is not valid JSON: {0}", [esc(err.message)]));
							return;
						}
					}
					const payload = standard
						? this.action_payload(action, { enabled: !!values.enabled, description: values.description || "" })
						: {
								is_new,
								tool_name: (values.tool_name || "").trim(),
								title: values.title || "",
								action_type: values.action_type,
								enabled: !!values.enabled,
								description: values.description || "",
								method: (values.method || "").trim(),
								input_schema: schema,
								read_only_hint: !!values.read_only_hint,
								destructive_hint: !!values.destructive_hint,
						  };
					const res = await this.call("save_action", { action: payload }, true);
					this.data.actions = res.actions;
					d.hide();
					frappe.show_alert({ message: __("Action {0} saved", [esc(res.name)]), indicator: "green" }, 4);
					this.render_nav();
					this.render_actions();
				},
			});
			if (action && !standard) {
				d.set_secondary_action_label(__("Delete"));
				d.set_secondary_action(() => {
					const used = action.used_by.length ? " " + __("It will be removed from {0}.", [esc(action.used_by.join(", "))]) : "";
					frappe.confirm(__("Delete the action {0}?", [bold(action.name)]) + used, async () => {
						const res = await this.call("delete_action", { name: action.name }, true);
						this.data.actions = res.actions;
						d.hide();
						this.render_nav();
						this.render_actions();
					});
				});
			}
			d.show();
		}

		// ===================================================================== inspector
		render_inspector(user) {
			set_html(
				this.$main,
				`<header class="mcpc-head">
					<div>
						<h1 class="mcpc-title">${esc(__("Access inspector"))}</h1>
						<p class="mcpc-lede">${esc(__("See exactly what a user's AI client can do: which profiles apply and why, which tools it receives, and where ERPNext permissions narrow it further."))}</p>
					</div>
				</header>
				<div class="mcpc-adder mcpc-adder--wide mcpc-inspect-pick" data-adder="inspect"></div>
				<div class="mcpc-inspect"></div>`
			);
			const control = this.make_adder(
				this.$main.querySelector('[data-adder="inspect"]'),
				"User",
				__("Choose a user to inspect"),
				(value) => frappe.set_route("mcp-console", "inspector", value),
				{ filters: { name: ["!=", "Guest"] } }
			);
			if (user) {
				control.$input.val(user);
				this.run_inspect(user);
			} else {
				set_html(
					this.$main.querySelector(".mcpc-inspect"),
					`<div class="mcpc-empty mcpc-empty--inline"><p>${esc(__("Tip: inspect your integration user after every profile change."))}</p></div>`
				);
			}
		}

		async run_inspect(user) {
			const $out = this.$main.querySelector(".mcpc-inspect");
			set_html($out, `<div class="mcpc-loading">${esc(__("Inspecting {0}…", [user]))}</div>`);
			let r;
			try {
				r = await this.call("inspect_user", { user });
			} catch (e) {
				set_html($out, "");
				return;
			}
			if (this.section !== "inspector") return;
			const cell_html = (state) =>
				({
					allowed: `<span class="mcpc-state is-ok">${icon("check", 13)}<span class="sr-only">${esc(__("allowed"))}</span></span>`,
					erp_denied: `<span class="mcpc-state is-warn">${esc(__("role"))}</span>`,
					locked: `<span class="mcpc-state is-muted">${icon("lock", 13)}<span class="sr-only">${esc(__("locked"))}</span></span>`,
					none: `<span class="mcpc-state is-none"><span aria-hidden="true">·</span><span class="sr-only">${esc(__("not granted"))}</span></span>`,
				})[state];
			set_html(
				$out,
				`<section class="mcpc-person">
					<div>
						<h2 class="mcpc-person__name">${esc(r.full_name || r.user)}</h2>
						<p class="mcpc-mono mcpc-muted">${esc(r.user)}</p>
					</div>
					<div class="mcpc-person__pills">
						${r.enabled ? "" : `<span class="mcpc-pill mcpc-pill--bad">${esc(__("User disabled"))}</span>`}
						${r.has_access ? `<span class="mcpc-pill mcpc-pill--ok">${esc(__("Has MCP access"))}</span>` : `<span class="mcpc-pill mcpc-pill--warn">${esc(__("No MCP access"))}</span>`}
					</div>
				</section>
				<div class="mcpc-inspect__grid">
					<section>
						<h3 class="mcpc-h3">${esc(__("Profiles"))}</h3>
						${
							r.profiles.length
								? `<ul class="mcpc-reasons">${r.profiles
										.map(
											(p) => `<li class="${p.enabled ? "" : "is-off"}"><button type="button" class="mcpc-linkbtn" data-act="goto" data-section="profiles" data-arg="${esc(p.name)}">${esc(p.name)}</button>
										<span>${esc(p.reasons.map((x) => (x.type === "user" ? __("assigned directly") : __("via role {0}", [x.value]))).join(", "))}</span>
										${p.enabled ? "" : `<span class="mcpc-pill mcpc-pill--muted">${esc(__("disabled"))}</span>`}</li>`
										)
										.join("")}</ul>`
								: `<p class="mcpc-muted">${esc(__("No profile lists this user or any of their roles, so every MCP request is refused with 403."))}</p>`
						}
						<h3 class="mcpc-h3">${esc(__("Action types"))}</h3>
						<p class="mcpc-pills">${r.action_types.length ? r.action_types.map((t) => `<span class="mcpc-pill mcpc-pill--muted">${esc(TYPE_INFO[t][0])}</span>`).join("") : `<span class="mcpc-muted">${esc(__("None"))}</span>`}</p>
						<h3 class="mcpc-h3">${esc(__("Reports"))}</h3>
						${
							r.reports.length
								? `<ul class="mcpc-reasons">${r.reports.map((x) => `<li><span>${esc(x.report)}</span>${x.permitted ? "" : `<span class="mcpc-pill mcpc-pill--warn">${esc(__("blocked by ERPNext role"))}</span>`}</li>`).join("")}</ul>`
								: `<p class="mcpc-muted">${esc(__("None"))}</p>`
						}
						<details class="mcpc-details"><summary>${esc(__("ERPNext roles ({0})", [r.roles.length]))}</summary><p class="mcpc-muted">${esc(r.roles.join(", "))}</p></details>
					</section>
					<section>
						<h3 class="mcpc-h3">${esc(__("DocType access"))}</h3>
						${
							r.doctypes.length
								? `<div class="mcpc-tablewrap"><table class="mcpc-matrix mcpc-matrix--compact"><thead><tr><th scope="col">${esc(__("DocType"))}</th>${OPS.map((op) => `<th scope="col">${esc(__(op))}</th>`).join("")}</tr></thead>
							<tbody>${r.doctypes
								.map(
									(d) => `<tr><th scope="row"><span class="mcpc-matrix__name">${esc(d.doctype)}</span>${d.scoped ? ` <span class="mcpc-pill mcpc-pill--info">${esc(__("scoped"))}</span>` : ""}${d.fields_limited ? ` <span class="mcpc-pill mcpc-pill--info">${esc(__("fields limited"))}</span>` : ""}</th>
									${OPS.map((op) => `<td class="mcpc-cell">${cell_html(d.operations[op])}</td>`).join("")}</tr>`
								)
								.join("")}</tbody></table></div>
							<p class="mcpc-legend"><span>${icon("check", 12)} ${esc(__("allowed"))}</span><span><span class="mcpc-state is-warn">${esc(__("role"))}</span>${esc(__("granted by a profile but refused by ERPNext role permissions"))}</span><span>${icon("lock", 12)} ${esc(__("read-only over MCP"))}</span></p>`
								: `<p class="mcpc-muted">${esc(__("No DocTypes."))}</p>`
						}
						<h3 class="mcpc-h3">${esc(__("Tools the AI receives ({0})", [r.tools.length]))}</h3>
						${r.tools.length ? this.tool_list(r.tools) : `<p class="mcpc-muted">${esc(__("None."))}</p>`}
					</section>
				</div>`
			);
		}

		// ===================================================================== activity
		render_activity() {
			const a = this.activity;
			set_html(
				this.$main,
				`<header class="mcpc-head">
					<div>
						<h1 class="mcpc-title">${esc(__("Activity"))}</h1>
						<p class="mcpc-lede">${esc(__("Every MCP tool call: who made it, what it touched and how it ended. Secrets in arguments are masked."))}</p>
					</div>
					<a class="btn btn-default btn-sm" href="/app/mcp-audit-log">${icon("external", 14)}<span>${esc(__("Open audit log"))}</span></a>
				</header>
				<div class="mcpc-filters">
					${segmented("log-status", [["", __("All")], ["Success", __("Success")], ["Denied", __("Denied")], ["Error", __("Error")]], a.status, __("Result"))}
					<div class="mcpc-adder" data-adder="log-user"></div>
					<select class="form-control" data-filter="tool" aria-label="${esc(__("Action"))}">
						<option value="">${esc(__("Any action"))}</option>
						${this.data.actions.map((x) => `<option value="${esc(x.name)}" ${x.name === a.tool ? "selected" : ""}>${esc(x.name)}</option>`).join("")}
					</select>
					<select class="form-control" data-filter="hours" aria-label="${esc(__("Period"))}">
						${[[1, __("Last hour")], [24, __("Last 24 hours")], [168, __("Last 7 days")], [720, __("Last 30 days")], [0, __("All time")]]
							.map(([v, l]) => `<option value="${v}" ${v === a.hours ? "selected" : ""}>${esc(l)}</option>`)
							.join("")}
					</select>
				</div>
				<div class="mcpc-logs"><div class="mcpc-loading">${esc(__("Loading…"))}</div></div>`
			);
			const control = this.make_adder(this.$main.querySelector('[data-adder="log-user"]'), "User", __("Any user"), (value) => {
				this.activity.user = value;
				control.$input.val(value);
				this.load_logs(true);
			});
			if (a.user) control.$input.val(a.user);
			control.$input.on("input", () => {
				if (!control.$input.val() && this.activity.user) {
					this.activity.user = "";
					this.load_logs(true);
				}
			});
			this.$main.querySelectorAll("[data-filter]").forEach((el) =>
				el.addEventListener("change", () => {
					this.activity[el.dataset.filter] = el.dataset.filter === "hours" ? Number(el.value) : el.value;
					this.load_logs(true);
				})
			);
			this.load_logs(true);
		}

		async load_logs(reset) {
			const a = this.activity;
			const start = reset ? 0 : a.rows.length;
			const res = await this.call("logs", { query: { status: a.status, user: a.user, tool: a.tool, hours: a.hours, start, limit: 50 } });
			a.rows = reset ? res.rows : a.rows.concat(res.rows);
			a.has_more = res.has_more;
			const $logs = this.$main.querySelector(".mcpc-logs");
			if (!$logs) return;
			set_html(
				$logs,
				a.rows.length
					? this.log_table(a.rows, false) +
							(a.has_more ? `<div class="mcpc-more"><button type="button" class="btn btn-default btn-sm" data-act="more-logs">${esc(__("Load more"))}</button></div>` : "")
					: `<div class="mcpc-empty mcpc-empty--inline"><p class="mcpc-empty__title">${esc(__("No activity matches these filters"))}</p><p>${esc(__("Try a wider period or clear the filters."))}</p></div>`
			);
		}

		log_table(rows, compact) {
			if (!rows.length) return `<p class="mcpc-muted">${esc(__("No calls yet."))}</p>`;
			const target = (r) =>
				r.document_type
					? `${esc(r.document_type)}${r.document_name ? ` <span class="mcpc-mono">${esc(r.document_name)}</span>` : ""}`
					: r.report
					? esc(r.report)
					: `<span class="mcpc-muted">—</span>`;
			return `<div class="mcpc-tablewrap"><table class="mcpc-log">
				<thead><tr><th scope="col">${esc(__("When"))}</th><th scope="col">${esc(__("User"))}</th><th scope="col">${esc(__("Action"))}</th><th scope="col">${esc(__("Target"))}</th><th scope="col">${esc(__("Result"))}</th>${compact ? "" : `<th scope="col" class="is-num">${esc(__("Time"))}</th>`}</tr></thead>
				<tbody>${rows
					.map(
						(r) => `<tr>
						<td><button type="button" class="mcpc-linkbtn mcpc-log__open" data-act="open-log" data-name="${esc(r.name)}" title="${esc(r.creation)}">${esc(when(r.creation))}</button></td>
						<td class="mcpc-log__user">${esc(r.user)}</td>
						<td><span class="mcpc-mono">${esc(r.tool || r.rpc_method || "—")}</span></td>
						<td class="mcpc-log__target">${target(r)}</td>
						<td>${status_pill(r.status)}${!compact && r.error ? `<span class="mcpc-log__err">${esc(r.error)}</span>` : ""}</td>
						${compact ? "" : `<td class="is-num mcpc-mono">${r.duration_ms === null || r.duration_ms === undefined ? "" : esc(r.duration_ms) + " ms"}</td>`}
					</tr>`
					)
					.join("")}</tbody></table></div>`;
		}

		async log_dialog(name) {
			const r = await this.call("log_detail", { name });
			let args = r.arguments || "";
			try {
				args = args ? JSON.stringify(JSON.parse(args), null, 2) : "";
			} catch (e) {
				// Truncated JSON: show it as stored.
			}
			const row = (label, value, mono) => (value ? `<div><dt>${esc(label)}</dt><dd class="${mono ? "mcpc-mono" : ""}">${esc(value)}</dd></div>` : "");
			const d = new frappe.ui.Dialog({
				title: esc(`${r.tool || r.rpc_method || __("Request")} · ${__(r.status)}`),
				size: "large",
				fields: [{ fieldtype: "HTML", fieldname: "body" }],
			});
			const $body = d.fields_dict.body.$wrapper.get(0);
			$body.classList.add("mcpc", "mcpc-dialog");
			set_html(
				$body,
				`<div class="mcpc-logd__head">${status_pill(r.status)}<span class="mcpc-muted">${esc(r.creation)}</span></div>
				<dl class="mcpc-meta">
					${row(__("User"), r.user)}${row(__("Action"), r.tool, true)}${row(__("Action type"), r.action_type)}
					${row(__("DocType"), r.document_type)}${row(__("Document"), r.document_name, true)}${row(__("Report"), r.report)}
					${row(__("Duration"), r.duration_ms !== null && r.duration_ms !== undefined ? `${r.duration_ms} ms` : "")}${row(__("Profiles"), r.profiles)}
					${row(__("Client IP"), r.client_ip, true)}${row(__("Client"), r.user_agent)}
				</dl>
				${r.result_summary ? `<h3 class="mcpc-h3">${esc(__("Result"))}</h3><p>${esc(r.result_summary)}</p>` : ""}
				${r.error ? `<h3 class="mcpc-h3">${esc(__("Error"))}</h3><pre class="mcpc-pre mcpc-pre--bad">${esc(r.error)}</pre>` : ""}
				${args ? `<h3 class="mcpc-h3">${esc(__("Arguments"))}</h3><pre class="mcpc-pre">${esc(args)}</pre>` : ""}`
			);
			d.show();
		}

		// ===================================================================== connect
		render_connect() {
			const c = this.connect;
			set_html(
				this.$main,
				`<header class="mcpc-head">
					<div>
						<h1 class="mcpc-title">${esc(__("Connect a client"))}</h1>
						<p class="mcpc-lede">${esc(__("MCP clients connect over Streamable HTTP and sign in as a real ERPNext user with an API key. That user's roles pick the access profile, and their ERPNext permissions still apply."))}</p>
					</div>
				</header>
				<ol class="mcpc-flow">
					<li>
						<h2 class="mcpc-h3">${esc(__("Endpoint"))}</h2>
						<div class="mcpc-endpoint mcpc-endpoint--lg"><code>${esc(this.data.endpoint)}</code>
						<button type="button" class="btn btn-default btn-sm" data-act="copy-endpoint">${icon("copy", 14)}<span>${esc(__("Copy"))}</span></button></div>
						<p class="mcpc-help">${esc(__("Stateless JSON responses: no session or event stream is needed. Serve the site over HTTPS before connecting clients across the internet."))}</p>
					</li>
					<li>
						<h2 class="mcpc-h3">${esc(__("Credentials"))}</h2>
						<p class="mcpc-help">${esc(__("Use a dedicated integration user, so its roles — and therefore its profile — are exactly what the AI needs."))}</p>
						<div class="mcpc-keyrow"><div class="mcpc-adder mcpc-adder--wide" data-adder="connect-user"></div>
						${this.data.can_generate_keys ? `<button type="button" class="btn btn-default btn-sm" data-act="generate-keys" ${c.user ? "" : "disabled"}>${esc(__("Generate API key"))}</button>` : ""}</div>
						${this.data.can_generate_keys ? "" : `<p class="mcpc-help">${esc(__("Only a System Manager can generate API keys. Ask one, or have the user generate keys from their User settings."))}</p>`}
						<div class="mcpc-connect-user" aria-live="polite"></div>
						<div class="mcpc-keys"></div>
					</li>
					<li>
						<h2 class="mcpc-h3">${esc(__("Client configuration"))}</h2>
						${segmented("client", [["claude_code", "Claude Code"], ["claude_desktop", "Claude Desktop"], ["cursor", "Cursor"], ["vscode", "VS Code"], ["http", "HTTP / curl"]], c.client, __("Client"))}
						<div class="mcpc-snippet"></div>
					</li>
					<li>
						<h2 class="mcpc-h3">${esc(__("Test the connection"))}</h2>
						<p class="mcpc-help">${esc(__("Runs initialize and tools/list against the endpoint — with the key generated above, or as you when there is none."))}</p>
						<button type="button" class="btn btn-default btn-sm" data-act="run-test">${esc(__("Run test"))}</button>
						<div class="mcpc-test" aria-live="polite"></div>
					</li>
				</ol>`
			);
			const control = this.make_adder(
				this.$main.querySelector('[data-adder="connect-user"]'),
				"User",
				__("Integration user"),
				(value) => {
					c.user = value;
					c.keys = null;
					control.$input.val(value);
					this.render_connect_keys();
					this.check_connect_user();
					const $gen = this.$main.querySelector('[data-act="generate-keys"]');
					if ($gen) $gen.disabled = false;
				},
				{ filters: { enabled: 1, name: ["!=", "Guest"] } }
			);
			if (c.user) {
				control.$input.val(c.user);
				this.check_connect_user();
			}
			this.render_connect_keys();
			this.render_test();
		}

		async check_connect_user() {
			const $el = this.$main.querySelector(".mcpc-connect-user");
			if (!$el || !this.connect.user) return;
			try {
				const r = await this.call("inspect_user", { user: this.connect.user });
				const enabled = r.profiles.filter((p) => p.enabled).map((p) => p.name);
				set_html(
					$el,
					r.has_access
						? `<p class="mcpc-okline">${icon("check", 14)}${esc(__("{0} gets {1} through {2}.", [r.user, plural(r.tools.length, __("tool"), __("tools")), enabled.join(", ")]))}</p>`
						: `<p class="mcpc-warnline">${icon("alert", 14)}${esc(__("No enabled profile applies to {0} yet, so requests will get 403. Add one of their roles, or the user, to a profile.", [r.user]))}</p>`
				);
			} catch (e) {
				set_html($el, "");
			}
		}

		render_connect_keys() {
			const $keys = this.$main.querySelector(".mcpc-keys");
			if (!$keys) return;
			const k = this.connect.keys;
			set_html(
				$keys,
				k
					? `<div class="mcpc-secret">
					<p class="mcpc-warnline">${icon("alert", 14)}${esc(__("Copy the secret now — it is not shown again. Any earlier secret of this user no longer works."))}</p>
					<dl class="mcpc-meta">
						<div><dt>${esc(__("API key"))}</dt><dd class="mcpc-mono">${esc(k.api_key)} <button type="button" class="mcpc-iconbtn" data-act="copy-text" data-text="${esc(k.api_key)}" data-what="${esc(__("API key"))}" aria-label="${esc(__("Copy API key"))}">${icon("copy", 14)}</button></dd></div>
						<div><dt>${esc(__("API secret"))}</dt><dd class="mcpc-mono">${esc(k.api_secret)} <button type="button" class="mcpc-iconbtn" data-act="copy-text" data-text="${esc(k.api_secret)}" data-what="${esc(__("API secret"))}" aria-label="${esc(__("Copy API secret"))}">${icon("copy", 14)}</button></dd></div>
					</dl></div>`
					: ""
			);
			this.render_connect_snippet();
		}

		generate_keys() {
			const user = this.connect.user;
			if (!user) return;
			frappe.confirm(__("Generate a new API secret for {0}? Integrations already using this user's current secret stop working.", [bold(user)]), async () => {
				this.connect.keys = await this.call("generate_keys", { user }, true);
				this.render_connect_keys();
			});
		}

		render_connect_snippet() {
			const $s = this.$main.querySelector(".mcpc-snippet");
			if (!$s) return;
			const k = this.connect.keys;
			const token = k ? `token ${k.api_key}:${k.api_secret}` : "token <api_key>:<api_secret>";
			const url = this.data.endpoint;
			const name = "erpnext";
			const snippet = {
				claude_code: { where: __("Run in a terminal:"), code: `claude mcp add --transport http ${name} ${url} --header "Authorization: ${token}"` },
				claude_desktop: {
					where: __("Add to claude_desktop_config.json (Settings → Developer → Edit Config), then restart Claude Desktop. Needs Node.js for mcp-remote."),
					code: JSON.stringify({ mcpServers: { [name]: { command: "npx", args: ["-y", "mcp-remote", url, "--header", "Authorization:${AUTH_HEADER}"], env: { AUTH_HEADER: token } } } }, null, 2),
				},
				cursor: { where: __("Add to ~/.cursor/mcp.json (or .cursor/mcp.json in a project):"), code: JSON.stringify({ mcpServers: { [name]: { url, headers: { Authorization: token } } } }, null, 2) },
				vscode: { where: __("Add to .vscode/mcp.json:"), code: JSON.stringify({ servers: { [name]: { type: "http", url, headers: { Authorization: token } } } }, null, 2) },
				http: {
					where: __("Any HTTP client can call the endpoint directly:"),
					code: `curl -X POST '${url}' \\\n  -H 'Authorization: ${token}' \\\n  -H 'Content-Type: application/json' \\\n  -H 'Accept: application/json, text/event-stream' \\\n  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'`,
				},
			}[this.connect.client];
			set_html(
				$s,
				`<p class="mcpc-help">${esc(snippet.where)}</p>
				<div class="mcpc-code"><pre>${esc(snippet.code)}</pre>
				<button type="button" class="btn btn-default btn-xs mcpc-code__copy" data-act="copy-text" data-text="${esc(snippet.code)}" data-what="${esc(__("Configuration"))}">${icon("copy", 13)}<span>${esc(__("Copy"))}</span></button></div>
				${k ? "" : `<p class="mcpc-help">${esc(__("Replace <api_key>:<api_secret> with the user's credentials, or generate them above to fill them in."))}</p>`}`
			);
		}

		async run_test() {
			const k = this.connect.keys;
			const headers = { "Content-Type": "application/json", Accept: "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18" };
			const opts = { method: "POST", headers };
			if (k) {
				headers.Authorization = `token ${k.api_key}:${k.api_secret}`;
				opts.credentials = "omit";
			} else {
				headers["X-Frappe-CSRF-Token"] = frappe.csrf_token;
				opts.credentials = "same-origin";
			}
			const steps = [];
			this.connect.test = { as: k ? this.connect.user : frappe.session.user, steps, running: true };
			this.render_test();
			const rpc = async (method, params) => {
				const res = await fetch(this.data.endpoint, { ...opts, body: JSON.stringify({ jsonrpc: "2.0", id: steps.length + 1, method, params }) });
				let body = null;
				try {
					body = await res.json();
				} catch (e) {
					// Not JSON (proxy error page): reported below by status.
				}
				return { status: res.status, body };
			};
			try {
				const init = await rpc("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "mcp-console-test", version: "1" } });
				if (init.status !== 200 || !init.body || init.body.error) {
					steps.push({ ok: false, text: __("initialize failed (HTTP {0}): {1}", [init.status, (init.body && init.body.error && init.body.error.message) || __("no JSON response")]) });
				} else {
					const info = init.body.result;
					steps.push({ ok: true, text: __("initialize — {0} {1}, protocol {2}", [info.serverInfo.title, info.serverInfo.version, info.protocolVersion]) });
					const list = await rpc("tools/list", {});
					if (list.status === 200 && list.body && list.body.result) {
						const names = list.body.result.tools.map((t) => t.name);
						steps.push({ ok: true, text: __("tools/list — {0}", [plural(names.length, __("tool"), __("tools"))]), detail: names.join(", ") });
					} else steps.push({ ok: false, text: __("tools/list failed (HTTP {0})", [list.status]) });
				}
			} catch (e) {
				steps.push({ ok: false, text: __("Could not reach the endpoint: {0}", [e.message]) });
			}
			this.connect.test.running = false;
			this.render_test();
		}

		render_test() {
			const $t = this.$main.querySelector(".mcpc-test");
			const t = this.connect.test;
			if (!$t || !t) return;
			set_html(
				$t,
				`<p class="mcpc-help">${esc(__("As {0}", [t.as]))}</p><ul class="mcpc-checks">${t.steps
					.map(
						(s) =>
							`<li class="${s.ok ? "is-ok" : "is-bad"}">${icon(s.ok ? "check" : "x", 14)}<span>${esc(s.text)}${s.detail ? `<small class="mcpc-mono">${esc(s.detail)}</small>` : ""}</span></li>`
					)
					.join("")}${t.running ? `<li class="is-pending">${esc(__("Running…"))}</li>` : ""}</ul>`
			);
		}

		// ===================================================================== settings
		settings_dirty() {
			return !!this.settings_draft && JSON.stringify(this.settings_payload()) !== JSON.stringify(this.data.settings);
		}

		settings_payload() {
			const s = this.settings_draft;
			return {
				enabled: !!s.enabled,
				server_name: s.server_name,
				instructions: s.instructions || "",
				default_page_size: Number(s.default_page_size),
				max_page_size: Number(s.max_page_size),
				max_report_rows: Number(s.max_report_rows),
				rate_limit_per_minute: Number(s.rate_limit_per_minute),
				audit_mode: s.audit_mode,
				log_arguments: !!s.log_arguments,
				log_retention_days: Number(s.log_retention_days),
				allowed_origins: Array.isArray(s.allowed_origins)
					? s.allowed_origins
					: String(s.allowed_origins || "")
							.split(/[\n,]+/)
							.map((x) => x.trim())
							.filter(Boolean),
			};
		}

		render_settings() {
			if (!this.settings_draft) this.settings_draft = clone(this.data.settings);
			const s = this.settings_draft;
			const origins = Array.isArray(s.allowed_origins) ? s.allowed_origins.join("\n") : s.allowed_origins;
			const num = (field, label, help, min, max) => `<div class="mcpc-field-row">
				<label class="mcpc-label" for="mcpc-set-${field}">${esc(label)}</label>
				<input id="mcpc-set-${field}" type="number" inputmode="numeric" class="form-control" min="${min}" max="${max}" value="${esc(s[field])}" data-bind="settings.${field}">
				<p class="mcpc-help">${esc(help)}</p></div>`;
			set_html(
				this.$main,
				`<header class="mcpc-head"><div><h1 class="mcpc-title">${esc(__("Settings"))}</h1>
				<p class="mcpc-lede">${esc(__("Server-wide behaviour. Who may do what is configured in profiles."))}</p></div></header>
				<div class="mcpc-settings">
					<section class="mcpc-setsec">
						<h2 class="mcpc-h2">${esc(__("Server"))}</h2>
						${switch_html({ checked: s.enabled, label: __("Accept MCP connections"), act: "toggle-setting", attrs: 'data-field="enabled"' })}
						<div class="mcpc-field-row"><label class="mcpc-label" for="mcpc-set-name">${esc(__("Server name"))}</label>
						<input id="mcpc-set-name" class="form-control" value="${esc(s.server_name)}" maxlength="140" data-bind="settings.server_name">
						<p class="mcpc-help">${esc(__("Shown to AI clients when they connect."))}</p></div>
						<div class="mcpc-field-row"><label class="mcpc-label" for="mcpc-set-ins">${esc(__("Instructions for AI clients"))}</label>
						<textarea id="mcpc-set-ins" class="form-control" rows="4" maxlength="8000" data-bind="settings.instructions" placeholder="${esc(__("Leave empty for the built-in guidance. Example: Our fiscal year starts in April. Never create documents for the company “ACME Demo”."))}">${esc(s.instructions)}</textarea>
						<p class="mcpc-help">${esc(__("Sent once when a client connects, before any tool call."))}</p></div>
					</section>
					<section class="mcpc-setsec">
						<h2 class="mcpc-h2">${esc(__("Limits"))}</h2>
						<div class="mcpc-grid2">
							${num("default_page_size", __("Default page size"), __("Rows list_documents returns when the AI does not ask for a number."), 1, 500)}
							${num("max_page_size", __("Maximum page size"), __("Upper bound for one list_documents page."), 1, 500)}
							${num("max_report_rows", __("Maximum report rows"), __("Longer report results are truncated and flagged."), 1, 10000)}
							${num("rate_limit_per_minute", __("Requests per minute per user"), __("0 turns rate limiting off."), 0, 100000)}
						</div>
					</section>
					<section class="mcpc-setsec">
						<h2 class="mcpc-h2">${esc(__("Audit"))}</h2>
						${segmented("audit-mode", [["All Requests", __("Every call")], ["Writes and Failures", __("Writes and failures")], ["Disabled", __("Off")]], s.audit_mode, __("Audit mode"))}
						<p class="mcpc-help">${esc(
							{
								"All Requests": __("Every tool call is logged, including reads."),
								"Writes and Failures": __("Successful reads are not logged; changes, denials and errors are."),
								Disabled: __("Nothing is logged. Not recommended."),
							}[s.audit_mode]
						)}</p>
						${switch_html({ checked: s.log_arguments, label: __("Log tool arguments (secrets are always masked)"), act: "toggle-setting", attrs: 'data-field="log_arguments"' })}
						<div class="mcpc-grid2">${num("log_retention_days", __("Keep logs for (days)"), __("0 keeps them forever. Older entries are removed daily."), 0, 3650)}</div>
					</section>
					<section class="mcpc-setsec">
						<h2 class="mcpc-h2">${esc(__("Security"))}</h2>
						<div class="mcpc-field-row"><label class="mcpc-label" for="mcpc-set-origins">${esc(__("Allowed browser origins"))}</label>
						<textarea id="mcpc-set-origins" class="form-control mcpc-mono" rows="3" data-bind="settings.allowed_origins" placeholder="http://localhost:6274">${esc(origins)}</textarea>
						<p class="mcpc-help">${esc(__("One per line. Only browser-based MCP clients such as MCP Inspector need this; desktop and CLI clients send no Origin and are always accepted."))}</p></div>
					</section>
				</div>
				<div class="mcpc-savebar mcpc-savebar--page" role="region" aria-label="${esc(__("Unsaved changes"))}" hidden>
					<span class="mcpc-savebar__msg">${esc(__("Unsaved changes"))}</span>
					<kbd class="mcpc-savebar__hint">Ctrl S</kbd>
					<button type="button" class="btn btn-default btn-sm" data-act="discard-settings">${esc(__("Discard"))}</button>
					<button type="button" class="btn btn-primary btn-sm" data-act="save-settings">${esc(__("Save settings"))}</button>
				</div>`
			);
			this.update_settings_savebar();
		}

		toggle_setting(field) {
			this.settings_draft[field] = !this.settings_draft[field];
			const $btn = this.$main.querySelector(`[data-act="toggle-setting"][data-field="${field}"]`);
			$btn.setAttribute("aria-checked", String(this.settings_draft[field]));
			this.update_settings_savebar();
		}

		update_settings_savebar() {
			const $bar = this.$main.querySelector(".mcpc-savebar--page");
			if ($bar) $bar.hidden = !this.settings_dirty();
		}

		async save_settings() {
			const payload = this.settings_payload();
			if (payload.default_page_size > payload.max_page_size) {
				frappe.msgprint(__("Default page size cannot exceed the maximum page size."));
				return;
			}
			this.data.settings = await this.call("save_settings", { settings: payload }, true);
			this.settings_draft = null;
			this.render_status();
			frappe.show_alert({ message: __("Settings saved"), indicator: "green" }, 4);
			this.render_settings();
		}
	};
})();
