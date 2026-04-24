"""HTTP routes for the Dynamic PPP Pricing API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import IPvAnyAddress

from dynamic_ppp_api.models import ErrorResponse, HealthResponse, PriceQuoteResponse
from dynamic_ppp_api.pricing import PricingService

router = APIRouter()


def get_pricing_service(request: Request) -> PricingService:
    """Retrieve the initialized pricing service from application state."""

    return request.app.state.runtime.pricing_service


@router.get(
    "/v1/ppp-price",
    response_model=PriceQuoteResponse,
    summary="Calculate a PPP-adjusted price",
    responses={
        404: {
            "model": ErrorResponse,
            "description": "The IP address was valid but could not be mapped to a country.",
        },
        503: {
            "model": ErrorResponse,
            "description": "A required data source is missing or unreadable.",
        },
    },
)
def get_ppp_price(
    base_price: Annotated[
        float,
        Query(gt=0.0, description="Base product price before PPP adjustment."),
    ],
    ip: Annotated[
        IPvAnyAddress,
        Query(description="Client IP address used for country detection."),
    ],
    pricing_service: Annotated[PricingService, Depends(get_pricing_service)],
) -> PriceQuoteResponse:
    """Return a country-aware suggested price using PPP discount data."""

    return pricing_service.quote(base_price=base_price, ip=str(ip))


@router.get("/healthz", response_model=HealthResponse, summary="Liveness probe")
def healthz() -> HealthResponse:
    """Indicate that the process is alive."""

    return HealthResponse(status="ok")


@router.get("/readyz", response_model=HealthResponse, summary="Readiness probe")
def readyz(request: Request) -> HealthResponse:
    """Indicate that startup completed and the runtime is ready."""

    _ = request.app.state.runtime
    return HealthResponse(status="ready")
