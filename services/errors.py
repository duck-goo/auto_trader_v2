"""Service layer exceptions."""

from __future__ import annotations


class ServiceError(RuntimeError):
    """Base class for service-layer errors."""


class DuplicateClientOrderIdError(ServiceError):
    """Raised when client_order_id generation keeps colliding with DB UNIQUE."""

    def __init__(self, *, client_order_id: str, attempts: int) -> None:
        super().__init__(
            f"Failed to generate unique client_order_id after {attempts} attempts: "
            f"last_value={client_order_id!r}"
        )
        self.client_order_id = client_order_id
        self.attempts = attempts

class InsufficientPositionError(ServiceError):
    """
    Raised internally when a sell order fails the pre-trade check.

    Not exposed to callers — OrderService catches it and converts
    to OrderResult(outcome=FAILED).
    """

    def __init__(self, *, symbol: str, available_qty: int, requested_qty: int) -> None:
        super().__init__(
            f"Insufficient position for sell: symbol={symbol}, "
            f"available={available_qty}, requested={requested_qty}"
        )
        self.symbol = symbol
        self.available_qty = available_qty
        self.requested_qty = requested_qty