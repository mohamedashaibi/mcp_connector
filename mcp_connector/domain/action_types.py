READ = "Read"
CREATE = "Create"
UPDATE = "Update"
DELETE = "Delete"
SUBMIT = "Submit"
CANCEL = "Cancel"
REPORT = "Report"
CUSTOM = "Custom"

ACTION_TYPES = (READ, CREATE, UPDATE, DELETE, SUBMIT, CANCEL, REPORT, CUSTOM)

# Action types that are granted per DocType row inside a profile.
DOC_OPERATIONS = (READ, CREATE, UPDATE, DELETE, SUBMIT, CANCEL)

# Frappe permission type backing each document operation.
FRAPPE_PTYPE = {
	READ: "read",
	CREATE: "create",
	UPDATE: "write",
	DELETE: "delete",
	SUBMIT: "submit",
	CANCEL: "cancel",
}

# Column name of the checkbox for each operation on "MCP Profile DocType".
OPERATION_FIELD = {
	READ: "can_read",
	CREATE: "can_create",
	UPDATE: "can_update",
	DELETE: "can_delete",
	SUBMIT: "can_submit",
	CANCEL: "can_cancel",
}

# Column name of the master switch for each action type on "MCP Profile".
ACTION_TYPE_FIELD = {t: f"allow_{t.lower()}" for t in ACTION_TYPES}

WRITE_TYPES = frozenset({CREATE, UPDATE, DELETE, SUBMIT, CANCEL, CUSTOM})
