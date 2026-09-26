"""JSON-RPC 2.0 envelopes as used by the Model Context Protocol."""

from dataclasses import dataclass
from typing import Any, Optional

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# Implementation-defined server errors (-32000 to -32099).
ACCESS_DENIED = -32001
RATE_LIMITED = -32029


class RpcError(Exception):
	def __init__(self, code: int, message: str, data: Any = None, request_id: Any = None):
		super().__init__(message)
		self.code = code
		self.message = message
		self.data = data
		self.request_id = request_id


@dataclass(frozen=True)
class RpcRequest:
	method: str
	params: dict
	id: Any = None
	is_notification: bool = False


def parse_message(message: Any) -> RpcRequest:
	if not isinstance(message, dict):
		raise RpcError(INVALID_REQUEST, "Request must be a JSON object (batching is not supported)")
	request_id = message.get("id")
	if message.get("jsonrpc") != "2.0":
		raise RpcError(INVALID_REQUEST, "jsonrpc must be \"2.0\"", request_id=request_id)
	if "id" in message and (isinstance(request_id, bool) or not isinstance(request_id, (str, int))):
		raise RpcError(INVALID_REQUEST, "id must be a string or integer", request_id=None)

	method = message.get("method")
	if method is None and ("result" in message or "error" in message):
		# A response to a server-initiated request; we never send any, so ignore it.
		return RpcRequest(method="", params={}, id=None, is_notification=True)
	if not isinstance(method, str) or not method:
		raise RpcError(INVALID_REQUEST, "method must be a non-empty string", request_id=request_id)

	params = message.get("params")
	if params is None:
		params = {}
	if not isinstance(params, dict):
		raise RpcError(INVALID_PARAMS, "params must be an object", request_id=request_id)
	return RpcRequest(method=method, params=params, id=request_id, is_notification="id" not in message)


def success(request_id: Any, result: dict) -> dict:
	return {"jsonrpc": "2.0", "id": request_id, "result": result}


def failure(request_id: Any, code: int, message: str, data: Optional[Any] = None) -> dict:
	error = {"code": code, "message": message}
	if data is not None:
		error["data"] = data
	return {"jsonrpc": "2.0", "id": request_id, "error": error}
