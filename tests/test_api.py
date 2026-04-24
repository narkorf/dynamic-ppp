"""Endpoint tests for the PPP pricing API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dynamic_ppp_api.config import Settings
from dynamic_ppp_api.exceptions import CountryNotFoundError
from dynamic_ppp_api.main import create_app
from dynamic_ppp_api.models import PppCountryRecord, PppSnapshot, PppSnapshotMetadata
from dynamic_ppp_api.pricing import PricingService
from dynamic_ppp_api.runtime import RuntimeServices
from dynamic_ppp_api.providers.ppp import WorldBankPppRepository


class FakeCountryResolver:
    """Simple in-memory resolver used by tests."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping

    def resolve_country(self, ip: str) -> str:
        try:
            return self.mapping[ip]
        except KeyError as exc:
            raise CountryNotFoundError(f"No country could be resolved for IP {ip}.") from exc

    def close(self) -> None:
        return None


@pytest.fixture()
def ppp_snapshot_path(tmp_path: Path) -> Path:
    """Create a valid PPP snapshot for tests."""

    snapshot = PppSnapshot(
        metadata=PppSnapshotMetadata(
            source="World Development Indicators",
            indicator="PA.NUS.PPPC.RF",
            source_url="https://api.worldbank.org",
            generated_at="2026-04-05T00:00:00Z",
            max_discount=0.80,
            country_count=3,
        ),
        countries={
            "US": PppCountryRecord(
                country_code="US",
                price_level_ratio=1.00,
                discount_fraction=0.00,
                source_year=2024,
            ),
            "IN": PppCountryRecord(
                country_code="IN",
                price_level_ratio=0.24,
                discount_fraction=0.76,
                source_year=2024,
            ),
            "BR": PppCountryRecord(
                country_code="BR",
                price_level_ratio=0.46,
                discount_fraction=0.54,
                source_year=2024,
            ),
        },
    )

    snapshot_path = tmp_path / "ppp_snapshot.json"
    snapshot_path.write_text(
        json.dumps(snapshot.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    return snapshot_path


@pytest.fixture()
def client(ppp_snapshot_path: Path) -> TestClient:
    """Create a test client with injected providers."""

    settings = Settings(
        geoip_db_path=Path("/tmp/test.mmdb"),
        ppp_data_path=ppp_snapshot_path,
        ppp_max_discount=0.80,
    )

    def runtime_factory(_: Settings) -> RuntimeServices:
        resolver = FakeCountryResolver(
            {
                "8.8.8.8": "US",
                "1.1.1.1": "IN",
                "9.9.9.9": "BR",
            }
        )
        repository = WorldBankPppRepository(ppp_snapshot_path)
        pricing_service = PricingService(resolver=resolver, repository=repository)
        return RuntimeServices(
            settings=settings,
            country_resolver=resolver,
            ppp_repository=repository,
            pricing_service=pricing_service,
        )

    with TestClient(create_app(settings=settings, runtime_factory=runtime_factory)) as api:
        yield api


def test_ppp_price_endpoint_success(client: TestClient) -> None:
    response = client.get("/v1/ppp-price", params={"base_price": 100, "ip": "1.1.1.1"})

    assert response.status_code == 200
    assert response.json() == {
        "country": "IN",
        "base_price": 100.0,
        "discount_percentage": 76.0,
        "suggested_price": 24.0,
    }


def test_ppp_price_endpoint_supports_multiple_countries(client: TestClient) -> None:
    response = client.get("/v1/ppp-price", params={"base_price": 80, "ip": "9.9.9.9"})

    assert response.status_code == 200
    assert response.json()["country"] == "BR"
    assert response.json()["suggested_price"] == 36.8


def test_invalid_ip_returns_validation_error(client: TestClient) -> None:
    response = client.get("/v1/ppp-price", params={"base_price": 100, "ip": "not-an-ip"})

    assert response.status_code == 422


def test_non_positive_base_price_returns_validation_error(client: TestClient) -> None:
    response = client.get("/v1/ppp-price", params={"base_price": 0, "ip": "8.8.8.8"})

    assert response.status_code == 422


def test_unknown_ip_returns_application_error(client: TestClient) -> None:
    response = client.get("/v1/ppp-price", params={"base_price": 100, "ip": "8.8.4.4"})

    assert response.status_code == 404
    assert response.json() == {
        "code": "COUNTRY_NOT_FOUND",
        "message": "No country could be resolved for IP 8.8.4.4.",
    }


def test_health_endpoints(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}
