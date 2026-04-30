"""Service layer exceptions."""

from __future__ import annotations


class ServiceError(RuntimeError):
    """Base class for service-layer errors."""


class MissingTiming1ConvergenceSignalsError(ServiceError):
    """Raised when timing1 intraday scans require missing convergence signals."""

    def __init__(self, *, trade_date: str) -> None:
        super().__init__(
            f"Timing1 convergence signals are missing for trade_date={trade_date!r}."
        )
        self.trade_date = trade_date


class MissingTiming2SetupSignalsError(ServiceError):
    """Raised when timing2 intraday scans require missing setup signals."""

    def __init__(self, *, trade_date: str) -> None:
        super().__init__(
            f"Timing2 setup signals are missing for trade_date={trade_date!r}."
        )
        self.trade_date = trade_date


class RuntimeLockBusyError(ServiceError):
    """Raised when another live process already holds a runtime lock."""

    def __init__(
        self,
        *,
        lock_name: str,
        owner_id: str,
        expires_at: str,
    ) -> None:
        super().__init__(
            "Runtime lock is already held by another process: "
            f"lock_name={lock_name!r}, owner_id={owner_id!r}, "
            f"expires_at={expires_at}"
        )
        self.lock_name = lock_name
        self.owner_id = owner_id
        self.expires_at = expires_at


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
