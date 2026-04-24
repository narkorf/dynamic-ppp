"""Pricing policy and service objects."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from dynamic_ppp_api.models import PriceQuoteResponse
from dynamic_ppp_api.providers.base import CountryResolver, PppRepository

TWO_PLACES = Decimal("0.01")


def derive_discount_from_price_level_ratio(
    price_level_ratio: float, max_discount: float
) -> float:
    """Clamp a PPP discount fraction derived from a World Bank price level ratio."""

    derived_discount = 1.0 - price_level_ratio
    return max(0.0, min(max_discount, derived_discount))


@dataclass(slots=True)
class PricingService:
    """Calculate localized prices from country and PPP data providers."""

    resolver: CountryResolver
    repository: PppRepository

    def quote(self, base_price: float, ip: str) -> PriceQuoteResponse:
        """Return a PPP-adjusted price quote for the given request inputs."""

        country_code = self.resolver.resolve_country(ip)
        record = self.repository.get_country_record(country_code)

        base_amount = Decimal(str(base_price))
        discount_fraction = Decimal(str(record.discount_fraction))
        suggested_price = (base_amount * (Decimal("1") - discount_fraction)).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
        discount_percentage = (discount_fraction * Decimal("100")).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )

        return PriceQuoteResponse(
            country=country_code,
            base_price=float(base_amount),
            discount_percentage=float(discount_percentage),
            suggested_price=float(suggested_price),
        )
