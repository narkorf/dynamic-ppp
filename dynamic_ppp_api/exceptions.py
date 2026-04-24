"""Domain-specific exceptions used by the API."""


class PricingApiError(Exception):
    """Base exception for handled API errors."""

    status_code = 400
    error_code = "PRICING_API_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class CountryResolutionError(PricingApiError):
    """Raised when an IP address cannot be processed by the resolver."""

    status_code = 400
    error_code = "COUNTRY_RESOLUTION_ERROR"


class CountryNotFoundError(PricingApiError):
    """Raised when a valid IP does not map to a country."""

    status_code = 404
    error_code = "COUNTRY_NOT_FOUND"


class PricingDataNotFoundError(PricingApiError):
    """Raised when PPP data does not exist for a resolved country."""

    status_code = 503
    error_code = "PPP_DATA_NOT_FOUND"


class DataSourceError(PricingApiError):
    """Raised when a required local data source is missing or unreadable."""

    status_code = 503
    error_code = "DATA_SOURCE_UNAVAILABLE"
