"""Custom exceptions for zhihu-cli."""


class LoginError(Exception):
    """Authentication failure."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class DataFetchError(Exception):
    """Failed to fetch data from Zhihu."""
