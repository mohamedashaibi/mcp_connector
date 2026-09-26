app_name = "mcp_connector"
app_title = "MCP Connector"
app_publisher = "Mohammed Ashaibi"
app_description = "Model Context Protocol server for ERPNext with role-based access profiles"
app_email = "mohashaibi2@gmail.com"
app_license = "MIT"
required_apps = ["frappe"]

before_install = "mcp_connector.infrastructure.install.before_install"
after_install = "mcp_connector.infrastructure.install.after_install"
after_migrate = "mcp_connector.infrastructure.install.after_migrate"

scheduler_events = {
	"daily": ["mcp_connector.infrastructure.audit.purge_old_logs"],
}
