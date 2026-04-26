"""Refresh local GeoIP MMDB and World Bank PPP data assets for deployment."""

from __future__ import annotations

import argparse
import csv
import json
import re
import tempfile
from datetime import UTC, datetime
from io import BytesIO, StringIO
from pathlib import Path
from zipfile import ZipFile
from urllib.parse import urlparse
from urllib.parse import quote
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import httpx
import pycountry

from dynamic_ppp_api.config import DEFAULT_DATA_DIR, Settings
from dynamic_ppp_api.models import PppCountryRecord, PppSnapshot, PppSnapshotMetadata
from dynamic_ppp_api.pricing import derive_discount_from_price_level_ratio

WORLD_BANK_SOURCE_ID = "2"
WORLD_BANK_INDICATOR_DOWNLOAD_URL = (
    "https://api.worldbank.org/v2/country/all/indicator/{indicator}"
    "?source={source_id}&downloadformat=csv"
)
DEFAULT_WORLD_BANK_INDICATOR = "PA.NUS.GDP.PLI"
WORLD_BANK_HEADERS = {
    "Accept": "*/*",
    "User-Agent": (
        "Mozilla/5.0 (compatible; DynamicPPPAPI/1.0; "
        "+https://datahelpdesk.worldbank.org/)"
    ),
}
IPLOCATE_DOWNLOAD_URL = (
    "https://www.iplocate.io/download/ip-to-country.mmdb"
    "?apikey={api_key}&variant={variant}"
)
YEAR_COLUMN_PATTERN = re.compile(r"^(?P<year>\d{4})(?:\s*\[YR\d{4}\])?$")


def fetch_binary(url: str) -> bytes:
    """Fetch a binary payload from a URL."""

    request = Request(url, headers=WORLD_BANK_HEADERS)
    try:
        with urlopen(request, timeout=60) as response:  # noqa: S310 - explicit trusted sources only
            return response.read()
    except HTTPError as exc:
        error_bytes = exc.read()
        body_preview = error_bytes.decode("utf-8", errors="replace")[:200]
        raise RuntimeError(
            f"World Bank API request failed with HTTP {exc.code} for {url}. "
            f"Response body started with: {body_preview!r}"
        ) from exc


def read_csv_rows_from_zip(bundle_bytes: bytes) -> dict[str, list[dict[str, str]]]:
    """Read CSV files from a World Bank download bundle."""

    csv_entries: dict[str, list[dict[str, str]]] = {}
    with ZipFile(BytesIO(bundle_bytes)) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(".csv"):
                continue
            text = archive.read(name).decode("utf-8-sig")
            csv_text = strip_world_bank_csv_preamble(text)
            reader = csv.DictReader(StringIO(csv_text))
            csv_entries[name] = [dict(row) for row in reader]
    if not csv_entries:
        raise RuntimeError("World Bank CSV download did not contain any CSV files.")
    return csv_entries


def strip_world_bank_csv_preamble(text: str) -> str:
    """Drop leading World Bank CSV preamble lines before the actual header row."""

    lines = text.splitlines()
    for index, line in enumerate(lines):
        parsed = next(csv.reader([line]), [])
        normalized = [part.strip().lower() for part in parsed]
        if "country code" not in normalized:
            continue
        if "indicator code" in normalized or any(is_year_column(part) for part in parsed):
            return "\n".join(lines[index:])

    return text


def find_indicator_data_rows(
    csv_entries: dict[str, list[dict[str, str]]], indicator: str
) -> list[dict[str, str]]:
    """Locate the data CSV rows for the requested indicator."""

    fallback_rows: list[dict[str, str]] | None = None
    for rows in csv_entries.values():
        if not rows:
            continue
        headers = set(rows[0].keys())
        if "Country Code" not in headers:
            continue
        year_headers = [header for header in headers if is_year_column(header)]
        if not year_headers:
            continue

        if "Indicator Code" in headers:
            matching_rows = [row for row in rows if row.get("Indicator Code") == indicator]
            if matching_rows:
                return matching_rows
            continue

        # Single-indicator World Bank CSV downloads may omit Indicator Code/Name columns.
        if fallback_rows is None:
            fallback_rows = rows

    if fallback_rows:
        return fallback_rows
    raise RuntimeError(
        f"World Bank CSV download did not contain data rows for indicator {indicator}."
    )


def resolve_iso2_country_code(iso3_code: str) -> str | None:
    """Resolve an ISO3 country code to ISO2 using the local pycountry database."""

    country = pycountry.countries.get(alpha_3=iso3_code.upper())
    if country and hasattr(country, "alpha_2"):
        return str(country.alpha_2).upper()
    return None


def is_year_column(header: str | None) -> bool:
    """Return whether a CSV header looks like a World Bank year column."""

    if not header:
        return False
    return YEAR_COLUMN_PATTERN.fullmatch(header.strip()) is not None


def parse_year_column(header: str) -> int | None:
    """Extract the year number from a World Bank year column header."""

    match = YEAR_COLUMN_PATTERN.fullmatch(header.strip())
    if not match:
        return None
    return int(match.group("year"))


def extract_latest_indicator_value(row: dict[str, str]) -> tuple[int, float] | None:
    """Extract the most recent non-empty yearly value from a World Bank indicator row."""

    latest: tuple[int, float] | None = None
    for key, value in row.items():
        year = parse_year_column(key) if key else None
        if year is None:
            continue
        raw_value = str(value).strip()
        if not raw_value:
            continue
        parsed_value = float(raw_value)
        if latest is None or year > latest[0]:
            latest = (year, parsed_value)
    return latest


def normalize_price_level_ratio(indicator: str, value: float) -> float:
    """Normalize World Bank indicator values into the ratio scale used by pricing."""

    if indicator == DEFAULT_WORLD_BANK_INDICATOR:
        return value / 100.0
    return value


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


def build_ppp_snapshot(indicator: str, max_discount: float) -> PppSnapshot:
    """Fetch the latest PPP data and convert it into the local snapshot schema."""

    source_url = WORLD_BANK_INDICATOR_DOWNLOAD_URL.format(
        indicator=quote(indicator),
        source_id=WORLD_BANK_SOURCE_ID,
    )
    csv_entries = read_csv_rows_from_zip(fetch_binary(source_url))
    rows = find_indicator_data_rows(csv_entries, indicator)

    country_records: dict[str, PppCountryRecord] = {}
    for row in rows:
        iso3_code = str(row.get("Country Code", "")).strip().upper()
        if not iso3_code:
            continue
        country_code = resolve_iso2_country_code(iso3_code)
        if not country_code:
            continue

        latest_value = extract_latest_indicator_value(row)
        if latest_value is None:
            continue

        source_year, raw_value = latest_value
        price_level_ratio = normalize_price_level_ratio(indicator, raw_value)
        discount_fraction = derive_discount_from_price_level_ratio(
            price_level_ratio=price_level_ratio,
            max_discount=max_discount,
        )

        country_records[country_code] = PppCountryRecord(
            country_code=country_code,
            price_level_ratio=price_level_ratio,
            discount_fraction=discount_fraction,
            source_year=source_year,
        )

    snapshot = PppSnapshot(
        metadata=PppSnapshotMetadata(
            source="World Development Indicators",
            indicator=quote(indicator),
            max_discount=max_discount,
            source_url=source_url,
            generated_at=datetime.now(UTC),
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
        default=DEFAULT_WORLD_BANK_INDICATOR,
        help="World Bank indicator used to derive the PPP snapshot.",
    )
    parser.add_argument(
        "--max-discount",
        type=float,
        default=0.80,
        help="Maximum PPP discount fraction allowed in the generated snapshot.",
    )
    parser.add_argument(
        "--skip-geoip",
        action="store_true",
        help="Skip the GeoIP MMDB download and refresh only the PPP snapshot.",
    )
    return parser.parse_args()


def main() -> None:
    """Refresh both the GeoIP MMDB database and the PPP snapshot in one command."""

    args = parse_args()
    if not args.skip_geoip:
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
    if not args.skip_geoip:
        print(f"GeoIP MMDB written to {args.geoip_output.resolve()}")
    print(f"PPP snapshot written to {args.ppp_output.resolve()}")


if __name__ == "__main__":
    main()
