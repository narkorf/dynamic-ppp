"""Application runtime wiring."""

from __future__ import annotations

from dataclasses import dataclass

from dynamic_ppp_api.config import Settings
from dynamic_ppp_api.exceptions import DataSourceError
from dynamic_ppp_api.pricing import PricingService
from dynamic_ppp_api.providers.base import CountryResolver, PppRepository
from dynamic_ppp_api.providers.geoip_mmdb import MmdbCountryResolver
from dynamic_ppp_api.providers.ppp import WorldBankPppRepository


@dataclass(slots=True)
class RuntimeServices:
    """Concrete runtime dependencies loaded during app startup."""

    settings: Settings
    country_resolver: CountryResolver
    ppp_repository: PppRepository
    pricing_service: PricingService

    def close(self) -> None:
        """Release underlying resources when the app shuts down."""

        self.country_resolver.close()


def build_runtime(settings: Settings) -> RuntimeServices:
    """Construct validated runtime services from the configured local data files."""

    country_resolver = MmdbCountryResolver(settings.geoip_db_path)
    try:
        ppp_repository = WorldBankPppRepository(settings.ppp_data_path)
    except Exception:
        country_resolver.close()
        raise

    snapshot_metadata = ppp_repository.snapshot.metadata
    if snapshot_metadata.max_discount > settings.ppp_max_discount:
        country_resolver.close()
        raise DataSourceError(
            "PPP snapshot max_discount exceeds the configured PPP_MAX_DISCOUNT."
        )

    pricing_service = PricingService(
        resolver=country_resolver,
        repository=ppp_repository,
    )

    return RuntimeServices(
        settings=settings,
        country_resolver=country_resolver,
        ppp_repository=ppp_repository,
        pricing_service=pricing_service,
    )
