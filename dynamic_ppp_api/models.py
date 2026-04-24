"""Pydantic models for API responses and PPP snapshot validation."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]


class ErrorResponse(BaseModel):
    """Structured error response returned by handled API exceptions."""

    code: str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Human-readable error description.")


class HealthResponse(BaseModel):
    """Simple health response used by liveness and readiness probes."""

    status: str = Field(description="Health status string.")


class PriceQuoteResponse(BaseModel):
    """Response payload returned by the PPP pricing endpoint."""

    country: CountryCode = Field(description="ISO 3166-1 alpha-2 country code detected from the IP.")
    base_price: float = Field(description="Original base price received by the API.", examples=[100.0])
    discount_percentage: float = Field(
        description="PPP discount applied to the base price, expressed as a percentage.",
        examples=[75.0],
    )
    suggested_price: float = Field(
        description="Suggested localized price after the PPP discount is applied.",
        examples=[25.0],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "country": "IN",
                "base_price": 100.0,
                "discount_percentage": 75.0,
                "suggested_price": 25.0,
            }
        }
    )


class PppSnapshotMetadata(BaseModel):
    """Metadata stored alongside the PPP snapshot."""

    source: str = Field(description="Origin of the PPP dataset.")
    indicator: str = Field(description="World Bank indicator used to derive the snapshot.")
    source_url: str = Field(description="URL used to fetch the official source data.")
    generated_at: datetime = Field(description="Timestamp at which the snapshot was generated.")
    max_discount: float = Field(ge=0.0, le=1.0, description="Configured maximum discount fraction.")
    country_count: int = Field(ge=0, description="Number of country entries in the snapshot.")


class PppCountryRecord(BaseModel):
    """PPP record stored for a single country."""

    country_code: CountryCode = Field(description="ISO 3166-1 alpha-2 country code.")
    price_level_ratio: float = Field(ge=0.0, description="World Bank PPP price level ratio value.")
    discount_fraction: float = Field(ge=0.0, le=1.0, description="Derived PPP discount as a fraction.")
    source_year: int = Field(ge=1900, le=2100, description="Source year attached to the country record.")


class PppSnapshot(BaseModel):
    """Validated PPP snapshot stored on disk and loaded at startup."""

    metadata: PppSnapshotMetadata
    countries: dict[CountryCode, PppCountryRecord]

    @model_validator(mode="after")
    def validate_snapshot(self) -> "PppSnapshot":
        """Ensure the snapshot metadata matches the country records."""

        if self.metadata.country_count != len(self.countries):
            raise ValueError("country_count must match the number of country records")

        for code, record in self.countries.items():
            if record.country_code != code:
                raise ValueError(f"country record key mismatch for {code}")
            if record.discount_fraction > self.metadata.max_discount:
                raise ValueError(
                    f"discount_fraction for {code} exceeds snapshot max_discount"
                )

        return self
