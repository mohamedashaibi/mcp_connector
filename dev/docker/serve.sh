#!/usr/bin/env bash
# (Re)start the dev web server on :8000. The image ships without procps, so scan /proc.
for p in /proc/[0-9]*; do
	cmd=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null) || continue
	case "$cmd" in
		*"frappe.utils.bench_helper frappe --site"*" serve "*) kill "${p#/proc/}" 2>/dev/null ;;
	esac
done
sleep 1
cd /home/frappe/frappe-bench
exec bench --site "${SITE:-mcp.localhost}" serve --port 8000 --noreload > logs/serve.log 2>&1
