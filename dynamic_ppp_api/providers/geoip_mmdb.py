"""Generic MMDB-backed country resolver."""

from __future__ import annotations

from ipaddress import ip_address
from pathlib import Path
from typing import Any

import maxminddb

from dynamic_ppp_api.exceptions import CountryNotFoundError, CountryResolutionError, DataSourceError


class MmdbCountryResolver:
    """Resolve client country codes from a local GeoIP MMDB database."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)

        if not self.database_path.exists():
            raise DataSourceError(
                f"GeoIP MMDB database was not found at {self.database_path}"
            )

        try:
            self._reader = maxminddb.open_database(str(self.database_path))
        except Exception as exc:  # pragma: no cover - exercised in integration tests
            raise DataSourceError(
                f"Unable to open GeoIP MMDB database at {self.database_path}"
            ) from exc

    def resolve_country(self, ip: str) -> str:
        """Resolve an ISO country code from the supplied IP address."""

        try:
            ip_address(ip)
        except ValueError as exc:
            raise CountryResolutionError("The supplied IP address is not valid.") from exc

        try:
            record = self._reader.get(ip)
        except Exception as exc:  # pragma: no cover - depends on native reader state
            raise DataSourceError("The GeoIP MMDB resolver is unavailable.") from exc

        if not record:
            raise CountryNotFoundError(f"No country could be resolved for IP {ip}.")

        country_code = self._extract_country_code(record)
        if not country_code:
            raise CountryNotFoundError(f"No country could be resolved for IP {ip}.")

        return country_code.upper()

    def close(self) -> None:
        """Release the underlying MMDB reader."""

        self._reader.close()

    @staticmethod
    def _extract_country_code(record: dict[str, Any]) -> str | None:
        """Extract a country code from either flat or MaxMind-style MMDB records."""

        flat_country_code = record.get("country_code")
        if isinstance(flat_country_code, str) and flat_country_code:
            return flat_country_code

        country = record.get("country")
        if isinstance(country, dict):
            iso_code = country.get("iso_code")
            if isinstance(iso_code, str) and iso_code:
                return iso_code

        registered_country = record.get("registered_country")
        if isinstance(registered_country, dict):
            iso_code = registered_country.get("iso_code")
            if isinstance(iso_code, str) and iso_code:
                return iso_code

        represented_country = record.get("represented_country")
        if isinstance(represented_country, dict):
            iso_code = represented_country.get("iso_code")
            if isinstance(iso_code, str) and iso_code:
                return iso_code

        return None
