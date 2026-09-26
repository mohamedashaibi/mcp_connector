#!/usr/bin/env bash
# Runs inside the backend container as the frappe user. Idempotent.
set -euo pipefail
cd /home/frappe/frappe-bench
SITE="${SITE:-mcp.localhost}"

bench set-config -g db_host db >/dev/null
bench set-config -g redis_cache redis://redis:6379 >/dev/null
bench set-config -g redis_queue redis://redis:6379 >/dev/null
bench set-config -g redis_socketio redis://redis:6379 >/dev/null

grep -qx mcp_connector sites/apps.txt || printf '\nmcp_connector\n' >> sites/apps.txt
sed -i '/^$/d' sites/apps.txt
env/bin/pip install --quiet --disable-pip-version-check -e apps/mcp_connector

if [ ! -d "sites/$SITE" ]; then
	bench new-site "$SITE" --mariadb-root-password admin --admin-password admin --no-mariadb-socket
fi
if ! bench --site "$SITE" list-apps | grep -q mcp_connector; then
	bench --site "$SITE" install-app mcp_connector
fi
if [ "$(bench --site "$SITE" execute frappe.db.get_single_value --args '["System Settings", "setup_complete"]' | tail -1)" != "1" ]; then
	bench --site "$SITE" execute frappe.utils.install.complete_setup_wizard >/dev/null
fi
bench --site "$SITE" set-config allow_tests true >/dev/null
bench --site "$SITE" set-config server_script_enabled true >/dev/null
bench set-config -g server_script_enabled 1 >/dev/null  # v15 reads it from common_site_config only
bench --site "$SITE" set-config developer_mode 0 >/dev/null
echo "$SITE" > sites/currentsite.txt
echo "SETUP DONE $SITE"
