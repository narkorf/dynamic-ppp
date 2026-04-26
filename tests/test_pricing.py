"""Unit tests for pricing policy and runtime validation."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from dynamic_ppp_api.config import Settings
from dynamic_ppp_api.exceptions import CountryNotFoundError, DataSourceError
from dynamic_ppp_api.main import create_app
from dynamic_ppp_api import refresh_data
from dynamic_ppp_api.pricing import derive_discount_from_price_level_ratio
from dynamic_ppp_api.providers.geoip_mmdb import MmdbCountryResolver
from dynamic_ppp_api.runtime import build_runtime
from dynamic_ppp_api.providers import geoip_mmdb as geoip_mmdb_module
from dynamic_ppp_api.providers.ppp import WorldBankPppRepository


def test_discount_is_derived_from_price_level_ratio() -> None:
    assert derive_discount_from_price_level_ratio(0.50, 0.80) == 0.50
    assert derive_discount_from_price_level_ratio(1.20, 0.80) == 0.0
    assert derive_discount_from_price_level_ratio(0.01, 0.80) == 0.80


def test_mmdb_resolver_extracts_flat_iplocate_country_code() -> None:
    assert MmdbCountryResolver._extract_country_code({"country_code": "US"}) == "US"


def test_mmdb_resolver_extracts_nested_maxmind_country_code() -> None:
    assert MmdbCountryResolver._extract_country_code({"country": {"iso_code": "CA"}}) == "CA"


def test_mmdb_resolver_rejects_records_without_country_code() -> None:
    assert MmdbCountryResolver._extract_country_code({"continent_code": "NA"}) is None


def test_snapshot_missing_metadata_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad_snapshot.json"
    path.write_text(json.dumps({"countries": {}}), encoding="utf-8")

    with pytest.raises(DataSourceError):
        WorldBankPppRepository(path)


def test_invalid_country_code_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad_snapshot.json"
    path.write_text(
        json.dumps(
            {
                "metadata": {
                    "source": "World Development Indicators",
                    "indicator": "PA.NUS.PPPC.RF",
                    "source_url": "https://api.worldbank.org",
                    "generated_at": "2026-04-05T00:00:00Z",
                    "max_discount": 0.8,
                    "country_count": 1,
                },
                "countries": {
                    "USA": {
                        "country_code": "USA",
                        "price_level_ratio": 1.0,
                        "discount_fraction": 0.0,
                        "source_year": 2024,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(DataSourceError):
        WorldBankPppRepository(path)


def test_out_of_range_discount_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad_snapshot.json"
    path.write_text(
        json.dumps(
            {
                "metadata": {
                    "source": "World Development Indicators",
                    "indicator": "PA.NUS.PPPC.RF",
                    "source_url": "https://api.worldbank.org",
                    "generated_at": "2026-04-05T00:00:00Z",
                    "max_discount": 0.8,
                    "country_count": 1,
                },
                "countries": {
                    "US": {
                        "country_code": "US",
                        "price_level_ratio": 1.0,
                        "discount_fraction": 0.9,
                        "source_year": 2024,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(DataSourceError):
        WorldBankPppRepository(path)


def test_build_runtime_fails_when_geoip_database_is_missing(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "ppp_snapshot.json"
    snapshot_path.write_text(
        Path(
            "/Users/nanaarkorful/Documents/Dynamic Purchasing Power Parity API/dynamic_ppp_api/data/ppp_snapshot.json"
        ).read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    settings = Settings(
        geoip_db_path=tmp_path / "missing.mmdb",
        ppp_data_path=snapshot_path,
        ppp_max_discount=0.80,
    )

    with pytest.raises(DataSourceError):
        build_runtime(settings)


def test_build_runtime_fails_when_snapshot_is_corrupted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot_path = tmp_path / "ppp_snapshot.json"
    snapshot_path.write_text("{bad json", encoding="utf-8")
    database_path = tmp_path / "ip-to-country.mmdb"
    database_path.write_text("placeholder", encoding="utf-8")

    class FakeReader:
        def get(self, _: str) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        geoip_mmdb_module.maxminddb,
        "open_database",
        lambda _: FakeReader(),
    )

    settings = Settings(
        geoip_db_path=database_path,
        ppp_data_path=snapshot_path,
        ppp_max_discount=0.80,
    )

    with pytest.raises(DataSourceError):
        build_runtime(settings)


def test_mmdb_resolver_raises_not_found_for_missing_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "ip-to-country.mmdb"
    database_path.write_text("placeholder", encoding="utf-8")

    class FakeReader:
        def get(self, _: str) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        geoip_mmdb_module.maxminddb,
        "open_database",
        lambda _: FakeReader(),
    )

    resolver = MmdbCountryResolver(database_path)
    with pytest.raises(CountryNotFoundError):
        resolver.resolve_country("8.8.8.8")
    resolver.close()


def test_app_startup_fails_with_missing_dependencies(tmp_path: Path) -> None:
    settings = Settings(
        geoip_db_path=tmp_path / "missing.mmdb",
        ppp_data_path=tmp_path / "missing.json",
        ppp_max_discount=0.80,
    )

    with pytest.raises(DataSourceError):
        with TestClient(create_app(settings=settings)):
            pass


def test_legacy_maxmind_config_alias_is_still_accepted() -> None:
    settings = Settings(maxmind_db_path="/tmp/legacy.mmdb", _env_file=None)

    assert settings.geoip_db_path == Path("/tmp/legacy.mmdb")
    assert settings.maxmind_db_path == Path("/tmp/legacy.mmdb")


def test_build_iplocate_download_url_uses_api_key_and_variant() -> None:
    url = refresh_data.build_iplocate_download_url("secret key", "daily")

    assert "apikey=secret%20key" in url
    assert "variant=daily" in url


def build_world_bank_csv_bundle(
    *, data_csv: str, country_metadata_csv: str, indicator_metadata_csv: str = "Country Code,Indicator Name,Indicator Code\n"
) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("API_DATA.csv", data_csv)
        archive.writestr("Metadata_Country.csv", country_metadata_csv)
        archive.writestr("Metadata_Indicator.csv", indicator_metadata_csv)
    return buffer.getvalue()


def test_read_csv_rows_from_zip_returns_csv_contents() -> None:
    bundle = build_world_bank_csv_bundle(
        data_csv="Country Name,Country Code,Indicator Name,Indicator Code,2024\nUnited States,USA,PLI,PA.NUS.GDP.PLI,100\n",
        country_metadata_csv="Country Code,2-alpha code\nUSA,US\n",
    )

    rows = refresh_data.read_csv_rows_from_zip(bundle)

    assert "API_DATA.csv" in rows
    assert rows["API_DATA.csv"][0]["Country Code"] == "USA"


def test_read_csv_rows_from_zip_skips_world_bank_preamble_lines() -> None:
    bundle = build_world_bank_csv_bundle(
        data_csv=(
            "Data Source,World Development Indicators\n"
            "Last Updated Date,2026-04-01\n"
            "\n"
            "Country Name,Country Code,Indicator Name,Indicator Code,2024 [YR2024]\n"
            "United States,USA,PLI,PA.NUS.GDP.PLI,100\n"
        ),
        country_metadata_csv="Country Code,2-alpha code\nUSA,US\n",
    )

    rows = refresh_data.read_csv_rows_from_zip(bundle)

    assert rows["API_DATA.csv"][0]["Indicator Code"] == "PA.NUS.GDP.PLI"


def test_read_csv_rows_from_zip_raises_when_zip_has_no_csv_files() -> None:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("README.txt", "hello")

    with pytest.raises(RuntimeError, match="did not contain any CSV files"):
        refresh_data.read_csv_rows_from_zip(buffer.getvalue())


def test_normalize_price_level_ratio_for_gdp_price_level_index() -> None:
    assert refresh_data.normalize_price_level_ratio("PA.NUS.GDP.PLI", 100.0) == 1.0
    assert refresh_data.normalize_price_level_ratio("PA.NUS.GDP.PLI", 24.0) == 0.24


def test_normalize_price_level_ratio_preserves_ratio_indicator_values() -> None:
    assert refresh_data.normalize_price_level_ratio("PA.NUS.PPPC.RF", 0.24) == 0.24


def test_is_year_column_accepts_world_bank_year_formats() -> None:
    assert refresh_data.is_year_column("2024")
    assert refresh_data.is_year_column("2024 [YR2024]")
    assert not refresh_data.is_year_column("Country Code")


def test_parse_year_column_extracts_year_from_world_bank_header() -> None:
    assert refresh_data.parse_year_column("2024") == 2024
    assert refresh_data.parse_year_column("2024 [YR2024]") == 2024
    assert refresh_data.parse_year_column("Country Code") is None


def test_resolve_iso2_country_code_returns_iso2_code() -> None:
    assert refresh_data.resolve_iso2_country_code("USA") == "US"
    assert refresh_data.resolve_iso2_country_code("IND") == "IN"


def test_resolve_iso2_country_code_returns_none_for_unknown_code() -> None:
    assert refresh_data.resolve_iso2_country_code("ZZZ") is None


def test_find_indicator_data_rows_returns_matching_rows() -> None:
    rows = {
        "API_DATA.csv": [
            {"Country Code": "USA", "Indicator Code": "PA.NUS.GDP.PLI", "2024": "100"},
            {"Country Code": "USA", "Indicator Code": "OTHER", "2024": "5"},
        ]
    }

    assert refresh_data.find_indicator_data_rows(rows, "PA.NUS.GDP.PLI") == [
        {"Country Code": "USA", "Indicator Code": "PA.NUS.GDP.PLI", "2024": "100"}
    ]


def test_find_indicator_data_rows_accepts_single_indicator_csv_without_indicator_code() -> None:
    rows = {
        "API_DATA.csv": [
            {
                "Country Name": "United States",
                "Country Code": "USA",
                "2024 [YR2024]": "100",
            },
            {"Country Name": "India", "Country Code": "IND", "2024 [YR2024]": "24"},
        ]
    }

    assert refresh_data.find_indicator_data_rows(rows, "PA.NUS.GDP.PLI") == rows["API_DATA.csv"]


def test_find_indicator_data_rows_raises_when_indicator_missing() -> None:
    with pytest.raises(RuntimeError, match="did not contain data rows"):
        refresh_data.find_indicator_data_rows({}, "PA.NUS.GDP.PLI")


def test_extract_latest_indicator_value_returns_latest_non_empty_year() -> None:
    row = {"2022 [YR2022]": "", "2023 [YR2023]": "24.5", "2024 [YR2024]": "25.0"}

    assert refresh_data.extract_latest_indicator_value(row) == (2024, 25.0)


def test_extract_latest_indicator_value_returns_none_when_no_year_values() -> None:
    assert refresh_data.extract_latest_indicator_value({"Country Code": "USA"}) is None


def test_build_ppp_snapshot_normalizes_price_level_index_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = build_world_bank_csv_bundle(
        data_csv=(
            "Country Name,Country Code,Indicator Name,Indicator Code,2023 [YR2023],2024 [YR2024]\n"
            "United States,USA,Price level index (GDP),PA.NUS.GDP.PLI,99,100\n"
            "India,IND,Price level index (GDP),PA.NUS.GDP.PLI,23,24\n"
        ),
        country_metadata_csv="Country Code,TableName\nUSA,United States\nIND,India\n",
    )

    monkeypatch.setattr(refresh_data, "fetch_binary", lambda _: bundle)

    snapshot = refresh_data.build_ppp_snapshot("PA.NUS.GDP.PLI", 0.80)

    assert snapshot.metadata.indicator == "PA.NUS.GDP.PLI"
    assert "downloadformat=csv" in snapshot.metadata.source_url
    assert snapshot.countries["US"].price_level_ratio == 1.0
    assert snapshot.countries["US"].discount_fraction == 0.0
    assert snapshot.countries["IN"].price_level_ratio == 0.24
    assert snapshot.countries["IN"].discount_fraction == 0.76


def test_build_ppp_snapshot_raises_descriptive_error_for_missing_indicator_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = build_world_bank_csv_bundle(
        data_csv="Country Name,Country Code,Indicator Name,Indicator Code,2024\nUnited States,USA,Other,OTHER,100\n",
        country_metadata_csv="Country Code,TableName\nUSA,United States\n",
    )

    monkeypatch.setattr(refresh_data, "fetch_binary", lambda _: bundle)

    with pytest.raises(RuntimeError, match="did not contain data rows"):
        refresh_data.build_ppp_snapshot("PA.NUS.GDP.PLI", 0.80)


def test_fetch_binary_raises_runtime_error_for_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request, timeout: int):
        assert request.headers["Accept"] == "*/*"
        assert "DynamicPPPAPI/1.0" in request.headers["User-agent"]
        assert timeout == 60
        raise HTTPError(
            url="https://api.worldbank.org/test",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=BytesIO(b'{"message":"bad"}'),
        )

    monkeypatch.setattr(refresh_data, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="Response body started with"):
        refresh_data.fetch_binary("https://api.worldbank.org/test")


def test_download_geoip_database_writes_binary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination_path = tmp_path / "ip-to-country.mmdb"

    class FakeResponse:
        def __init__(self) -> None:
            self.url = "https://downloads.iplocate.io/ip-to-country.mmdb"
            self.headers = {"content-type": "application/octet-stream"}
            self.content = b"mmdb-bytes"

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def get(self, url: str) -> FakeResponse:
            assert "iplocate.io" in url
            return FakeResponse()

    monkeypatch.setattr(refresh_data.httpx, "Client", lambda **_: FakeClient())

    refresh_data.download_geoip_database(
        "https://www.iplocate.io/download/ip-to-country.mmdb",
        destination_path,
    )

    assert destination_path.read_bytes() == b"mmdb-bytes"


def test_missing_iplocate_api_key_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IPLOCATE_API_KEY", raising=False)
    monkeypatch.delenv("IPLOCATE_DOWNLOAD_URL", raising=False)

    with pytest.raises(SystemExit, match="IPLOCATE_API_KEY"):
        refresh_data.resolve_iplocate_download_url(Settings(_env_file=None))


def test_download_geoip_database_rejects_html_login_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination_path = tmp_path / "ip-to-country.mmdb"

    class FakeResponse:
        def __init__(self) -> None:
            self.url = "https://www.iplocate.io/login"
            self.headers = {"content-type": "text/html; charset=utf-8"}
            self.content = b"<html>login</html>"

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def get(self, url: str) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(refresh_data.httpx, "Client", lambda **_: FakeClient())

    with pytest.raises(RuntimeError, match="HTML page"):
        refresh_data.download_geoip_database(
            "https://www.iplocate.io/download/ip-to-country.mmdb",
            destination_path,
        )


def test_main_prints_written_file_locations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    geoip_output = tmp_path / "ip-to-country.mmdb"
    ppp_output = tmp_path / "ppp_snapshot.json"

    monkeypatch.setattr(
        refresh_data,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "geoip_output": geoip_output,
                "ppp_output": ppp_output,
                "world_bank_indicator": "PA.NUS.GDP.PLI",
                "max_discount": 0.80,
                "skip_geoip": False,
            },
        )(),
    )
    monkeypatch.setattr(
        refresh_data,
        "download_geoip_database",
        lambda download_url, destination_path: destination_path.write_bytes(b"mmdb-bytes"),
    )
    monkeypatch.setattr(
        refresh_data,
        "resolve_iplocate_download_url",
        lambda settings: "https://www.iplocate.io/download/ip-to-country.mmdb",
    )
    monkeypatch.setattr(
        refresh_data,
        "build_ppp_snapshot",
        lambda indicator, max_discount: refresh_data.PppSnapshot(
            metadata=refresh_data.PppSnapshotMetadata(
                source="World Development Indicators",
                indicator=indicator,
                source_url="https://api.worldbank.org",
                generated_at="2026-04-05T00:00:00Z",
                max_discount=max_discount,
                country_count=0,
            ),
            countries={},
        ),
    )

    refresh_data.main()

    output = capsys.readouterr().out
    assert str(geoip_output.resolve()) in output
    assert str(ppp_output.resolve()) in output


def test_main_can_refresh_ppp_without_geoip_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    geoip_output = tmp_path / "ip-to-country.mmdb"
    ppp_output = tmp_path / "ppp_snapshot.json"

    monkeypatch.setattr(
        refresh_data,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "geoip_output": geoip_output,
                "ppp_output": ppp_output,
                "world_bank_indicator": "PA.NUS.GDP.PLI",
                "max_discount": 0.80,
                "skip_geoip": True,
            },
        )(),
    )
    monkeypatch.setattr(
        refresh_data,
        "download_geoip_database",
        lambda *args, **kwargs: pytest.fail("GeoIP download should be skipped."),
    )
    monkeypatch.setattr(
        refresh_data,
        "resolve_iplocate_download_url",
        lambda settings: pytest.fail("IPLocate URL should not be resolved."),
    )
    monkeypatch.setattr(
        refresh_data,
        "build_ppp_snapshot",
        lambda indicator, max_discount: refresh_data.PppSnapshot(
            metadata=refresh_data.PppSnapshotMetadata(
                source="World Development Indicators",
                indicator=indicator,
                source_url="https://api.worldbank.org",
                generated_at="2026-04-05T00:00:00Z",
                max_discount=max_discount,
                country_count=0,
            ),
            countries={},
        ),
    )

    refresh_data.main()

    output = capsys.readouterr().out
    assert str(geoip_output.resolve()) not in output
    assert str(ppp_output.resolve()) in output
