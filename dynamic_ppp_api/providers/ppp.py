"""Repository for validated World Bank PPP snapshot data."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from dynamic_ppp_api.exceptions import DataSourceError, PricingDataNotFoundError
from dynamic_ppp_api.models import PppCountryRecord, PppSnapshot


class WorldBankPppRepository:
    """Load PPP discount data from a local JSON snapshot."""

    def __init__(self, snapshot_path: Path) -> None:
        self.snapshot_path = Path(snapshot_path)
        self._snapshot = self._load_snapshot()

    @property
    def snapshot(self) -> PppSnapshot:
        """Return the validated in-memory snapshot."""

        return self._snapshot

    def get_country_record(self, country_code: str) -> PppCountryRecord:
        """Return the PPP record for the requested country code."""

        code = country_code.upper()
        try:
            return self._snapshot.countries[code]
        except KeyError as exc:
            raise PricingDataNotFoundError(
                f"PPP pricing data is not available for country {code}."
            ) from exc

    def _load_snapshot(self) -> PppSnapshot:
        if not self.snapshot_path.exists():
            raise DataSourceError(
                f"PPP snapshot was not found at {self.snapshot_path}"
            )

        try:
            raw_snapshot = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DataSourceError(
                f"PPP snapshot at {self.snapshot_path} is not valid JSON"
            ) from exc
        except OSError as exc:
            raise DataSourceError(
                f"PPP snapshot at {self.snapshot_path} could not be read"
            ) from exc

        try:
            return PppSnapshot.model_validate(raw_snapshot)
        except ValidationError as exc:
            raise DataSourceError(
                f"PPP snapshot at {self.snapshot_path} failed schema validation"
            ) from exc
