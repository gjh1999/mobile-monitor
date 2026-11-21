"""Errors shared by the two CLI adapters."""


class RPCError(RuntimeError):
    def __init__(self, message='RPC request rejected', code=None, detail=None):
        super().__init__(message)
        self.code = code
        self.detail = detail


class RPCNotSent(ConnectionError):
    """The connection ended before writing any bytes of this request."""
