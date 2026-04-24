"""Protocols for replaceable runtime providers."""

from __future__ import annotations

from typing import Protocol

from dynamic_ppp_api.models import PppCountryRecord


class CountryResolver(Protocol):
    """Resolve a country code from an IP address."""

    def resolve_country(self, ip: str) -> str:
        """Return the ISO country code for the supplied IP address."""

    def close(self) -> None:
        """Release any provider resources."""


class PppRepository(Protocol):
    """Load PPP pricing data for a country."""

    def get_country_record(self, country_code: str) -> PppCountryRecord:
        """Return the PPP snapshot record for the supplied country code."""
