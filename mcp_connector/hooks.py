app_name = "mcp_connector"
app_title = "MCP Connector"
app_publisher = "Mohammed Ashaibi"
app_description = "Model Context Protocol server for ERPNext with role-based access profiles"
app_email = "mohashaibi2@gmail.com"
app_license = "MIT"
required_apps = ["frappe"]

after_install = "mcp_connector.infrastructure.install.after_install"
after_migrate = "mcp_connector.infrastructure.install.after_migrate"

# Only the Administrator may see or change the connector's configuration and audit trail,
# even if role permissions for these DocTypes are widened later.
has_permission = {
	"MCP Settings": "mcp_connector.infrastructure.permissions.administrator_only",
	"MCP Profile": "mcp_connector.infrastructure.permissions.administrator_only",
	"MCP Action": "mcp_connector.infrastructure.permissions.administrator_only",
	"MCP Audit Log": "mcp_connector.infrastructure.permissions.administrator_only",
}
permission_query_conditions = {
	"MCP Settings": "mcp_connector.infrastructure.permissions.administrator_only_query",
	"MCP Profile": "mcp_connector.infrastructure.permissions.administrator_only_query",
	"MCP Action": "mcp_connector.infrastructure.permissions.administrator_only_query",
	"MCP Audit Log": "mcp_connector.infrastructure.permissions.administrator_only_query",
}

# OAuth discovery documents under /.well-known/, so MCP clients (Claude connectors) can sign people in.
page_renderer = ["mcp_connector.api.discovery.OAuthDiscoveryRenderer"]

scheduler_events = {
	"daily": ["mcp_connector.infrastructure.audit.purge_old_logs"],
}
