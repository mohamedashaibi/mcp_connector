frappe.ui.form.on("MCP Profile", {
	setup(frm) {
		frm.set_query("document_type", "doctypes", () => ({
			filters: [
				["DocType", "istable", "=", 0],
				["DocType", "is_virtual", "=", 0],
			],
		}));
		frm.set_query("report", "reports", () => ({ filters: { disabled: 0 } }));
	},

	refresh(frm) {
		// "Add Multiple" pickers instead of adding rows one at a time.
		frm.get_field("doctypes").grid.set_multiple_add("document_type");
		frm.get_field("reports").grid.set_multiple_add("report");
		if (!frm.is_new()) {
			frm.add_custom_button(__("Open in MCP Console"), () => frappe.set_route("mcp-console", "profiles", frm.doc.name));
		}
	},
});
