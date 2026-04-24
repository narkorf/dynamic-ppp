"""Backward-compatible wrapper for the generic MMDB country resolver."""

from dynamic_ppp_api.providers.geoip_mmdb import MmdbCountryResolver

MaxMindCountryResolver = MmdbCountryResolver

__all__ = ["MaxMindCountryResolver", "MmdbCountryResolver"]
