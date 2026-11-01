class GradingError(Exception):
    """Safe application error; never wrap raw database/Docker exception text."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
