"""Refresh local GeoIP MMDB and World Bank PPP data assets for deployment."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.parse import quote
from urllib.request import urlopen

import httpx

from dynamic_ppp_api.config import DEFAULT_DATA_DIR, Settings
from dynamic_ppp_api.models import PppCountryRecord, PppSnapshot, PppSnapshotMetadata
from dynamic_ppp_api.pricing import derive_discount_from_price_level_ratio

WORLD_BANK_COUNTRIES_URL = "https://api.worldbank.org/v2/country?format=json&per_page=400"
WORLD_BANK_INDICATOR_URL = (
    "https://api.worldbank.org/v2/country/all/indicator/{indicator}"
    "?format=json&mrnev=1&per_page=20000"
)
IPLOCATE_DOWNLOAD_URL = (
    "https://www.iplocate.io/download/ip-to-country.mmdb"
    "?apikey={api_key}&variant={variant}"
)


def fetch_json(url: str) -> list[object]:
    """Fetch a JSON payload from a URL."""

    with urlopen(url) as response:  # noqa: S310 - explicit trusted sources only
        return json.load(response)


def atomic_write_text(path: Path, contents: str) -> None:
    """Write text to a file atomically."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=path.parent
    ) as handle:
        handle.write(contents)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def atomic_write_binary(path: Path, contents: bytes) -> None:
    """Write binary data to a file atomically."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", delete=False, dir=path.parent) as handle:
        handle.write(contents)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def build_country_code_lookup() -> dict[str, str]:
    """Build an ISO3 -> ISO2 lookup table from the World Bank country endpoint."""

    payload = fetch_json(WORLD_BANK_COUNTRIES_URL)
    countries = payload[1]
    lookup: dict[str, str] = {}
    for country in countries:
        iso2_code = country.get("iso2Code")
        iso3_code = country.get("id")
        if iso2_code and iso2_code != "NA" and iso3_code:
            lookup[str(iso3_code).upper()] = str(iso2_code).upper()
    return lookup


def build_ppp_snapshot(indicator: str, max_discount: float) -> PppSnapshot:
    """Fetch the latest PPP data and convert it into the local snapshot schema."""

    country_lookup = build_country_code_lookup()
    payload = fetch_json(WORLD_BANK_INDICATOR_URL.format(indicator=quote(indicator)))
    records = payload[1]

    country_records: dict[str, PppCountryRecord] = {}
    for row in records:
        value = row.get("value")
        iso3_code = str(row.get("countryiso3code", "")).upper()
        if value is None or not iso3_code or iso3_code not in country_lookup:
            continue

        country_code = country_lookup[iso3_code]
        price_level_ratio = float(value)
        discount_fraction = derive_discount_from_price_level_ratio(
            price_level_ratio=price_level_ratio,
            max_discount=max_discount,
        )

        country_records[country_code] = PppCountryRecord(
            country_code=country_code,
            price_level_ratio=price_level_ratio,
            discount_fraction=discount_fraction,
            source_year=int(row["date"]),
        )

    snapshot = PppSnapshot(
        metadata=PppSnapshotMetadata(
            source="World Development Indicators",
            indicator=indicator,
            source_url=WORLD_BANK_INDICATOR_URL.format(indicator=indicator),
            generated_at=datetime.now(UTC),
            max_discount=max_discount,
            country_count=len(country_records),
        ),
        countries=country_records,
    )
    return snapshot


def build_iplocate_download_url(api_key: str, variant: str) -> str:
    """Build the iplocate MMDB download URL from environment-supplied inputs."""

    return IPLOCATE_DOWNLOAD_URL.format(
        api_key=quote(api_key),
        variant=quote(variant),
    )


def resolve_iplocate_download_url(settings: Settings) -> str:
    """Resolve the effective iplocate MMDB download URL."""

    if settings.iplocate_download_url:
        return settings.iplocate_download_url

    if not settings.iplocate_api_key:
        raise SystemExit(
            "IPLOCATE_API_KEY must be set before refreshing GeoIP data, "
            "unless IPLOCATE_DOWNLOAD_URL is provided."
        )

    return build_iplocate_download_url(
        api_key=settings.iplocate_api_key,
        variant=settings.iplocate_variant,
    )


def download_geoip_database(download_url: str, destination_path: Path) -> None:
    """Download and install the requested GeoIP MMDB database."""

    headers = {
        "Accept": "*/*",
        "User-Agent": "DynamicPPPAPI/1.0 (+https://www.iplocate.io/docs/data-feeds)",
    }

    try:
        with httpx.Client(
            follow_redirects=True,
            headers=headers,
            timeout=60.0,
        ) as client:
            response = client.get(download_url)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"GeoIP MMDB download failed with HTTP {exc.response.status_code} from "
            f"{exc.request.url}"
        ) from exc
    except httpx.HTTPError as exc:
        raise RuntimeError("GeoIP MMDB download failed due to a network error.") from exc

    final_path = urlparse(str(response.url)).path
    content_type = response.headers.get("content-type", "").lower()
    contents = response.content

    if not contents:
        raise RuntimeError("Downloaded GeoIP MMDB file was empty.")

    if final_path.endswith("/login") or "text/html" in content_type:
        raise RuntimeError(
            "GeoIP MMDB download returned an HTML page instead of an MMDB file. "
            "This usually means the API key is invalid, the download URL is wrong, "
            "or the provider rejected the request."
        )

    if contents[:1] == b"<":
        raise RuntimeError(
            "GeoIP MMDB download returned text/HTML content instead of a binary MMDB file."
        )

    atomic_write_binary(destination_path, contents)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the refresh command."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ppp-output",
        type=Path,
        default=DEFAULT_DATA_DIR / "ppp_snapshot.json",
        help="Where to write the PPP snapshot JSON file.",
    )
    parser.add_argument(
        "--geoip-output",
        "--maxmind-output",
        dest="geoip_output",
        type=Path,
        default=DEFAULT_DATA_DIR / "ip-to-country.mmdb",
        help="Where to install the GeoIP MMDB country database.",
    )
    parser.add_argument(
        "--world-bank-indicator",
        default="PA.NUS.PPPC.RF",
        help="World Bank indicator used to derive the PPP snapshot.",
    )
    parser.add_argument(
        "--max-discount",
        type=float,
        default=0.80,
        help="Maximum PPP discount fraction allowed in the generated snapshot.",
    )
    return parser.parse_args()


def main() -> None:
    """Refresh both the GeoIP MMDB database and the PPP snapshot in one command."""

    args = parse_args()
    settings = Settings()
    download_geoip_database(
        download_url=resolve_iplocate_download_url(settings),
        destination_path=args.geoip_output,
    )

    snapshot = build_ppp_snapshot(
        indicator=args.world_bank_indicator,
        max_discount=args.max_discount,
    )
    atomic_write_text(
        args.ppp_output,
        json.dumps(snapshot.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
    )
    print(f"GeoIP MMDB written to {args.geoip_output.resolve()}")
    print(f"PPP snapshot written to {args.ppp_output.resolve()}")


if __name__ == "__main__":
    main()
