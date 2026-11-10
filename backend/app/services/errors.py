class DomainError(Exception):
    status_code = 400
    code = "domain_error"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message, self.details = message, details


class NotFound(DomainError):
    status_code, code = 404, "not_found"


class Conflict(DomainError):
    status_code, code = 409, "conflict"


class Unauthorized(DomainError):
    status_code, code = 401, "unauthorized"


class InvalidState(DomainError):
    status_code, code = 409, "invalid_state"
