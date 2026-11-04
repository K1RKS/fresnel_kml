"""Server configuration: which elevation sources to use, in which order."""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field

from .elevation import (ElevationChain, GoogleProvider, OpenElevationProvider, OpenTopoDataProvider,
                        USGSProvider, open_cache)
from .local_hgt import LocalHgtProvider

DEFAULT_CONFIG_PATH = "/etc/fresnelkml/config.json"

DEFAULT_CONFIG = {
    "elevation": {
        "chain": [
            {"name": "opentopodata", "type": "opentopodata",
             "url": "https://api.opentopodata.org/v1", "datasets": "ned10m,srtm30m",
             "batch": 100, "min_interval_s": 1.0},
            {"name": "open-elevation", "type": "open-elevation",
             "url": "https://api.open-elevation.com/api/v1/lookup"},
        ],
        "timeout_s": 10,
        "retries": 2,
        "cache_dir": None,
    },
    "map": {
        "tile_servers": [
            {"name": "OpenStreetMap", "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
             "attribution": "&copy; <a href=\"https://www.openstreetmap.org/copyright\">OpenStreetMap</a> "
                            "contributors",
             "max_zoom": 19},
            {"name": "OpenTopoMap", "url": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
             "attribution": "&copy; <a href=\"https://www.openstreetmap.org/copyright\">OpenStreetMap</a> "
                            "contributors, SRTM | style &copy; <a href=\"https://opentopomap.org\">OpenTopoMap</a> "
                            "(CC-BY-SA)",
             "max_zoom": 17},
        ],
        "default": "OpenStreetMap",
    },
    "nodes": {
        "feeds": [
            {"name": "K1RKS MeshMap", "url": "https://meshmap.k1rks.me/meshmap/data/nodes.json"},
        ],
        "cache_ttl_s": 300,
    },
}

SOURCE_TYPES = ("opentopodata", "open-elevation", "usgs", "google", "local_hgt")


class ConfigError(ValueError):
    pass


@dataclass
class Config:
    chain: list[dict] = field(default_factory=list)
    timeout_s: float = 10.0
    retries: int = 2
    cache_dir: str | None = None
    path: str | None = None
    tile_servers: list[dict] = field(default_factory=lambda: copy.deepcopy(DEFAULT_CONFIG["map"]["tile_servers"]))
    default_tile_server: str | None = DEFAULT_CONFIG["map"]["default"]
    node_feeds: list[dict] = field(default_factory=lambda: copy.deepcopy(DEFAULT_CONFIG["nodes"]["feeds"]))
    nodes_cache_ttl_s: float = DEFAULT_CONFIG["nodes"]["cache_ttl_s"]
    nodes_timeout_s: float | None = None

    def source_names(self) -> list[str]:
        return [s["name"] for s in self.chain]

    def public_sources(self) -> list[dict]:
        return [{"name": s["name"], "type": s["type"]} for s in self.chain]

    def public_map(self) -> dict:
        """Tile servers for the web page. Browsers fetch tiles directly, so the URLs are public."""
        return {"tile_servers": self.tile_servers, "default": self.default_tile_server}


_TILE_KEYS = {"name", "url", "attribution", "max_zoom", "subdomains"}


def _validate_tile_server(entry, index: int) -> dict:
    where = f"map.tile_servers[{index}]"
    if not isinstance(entry, dict):
        raise ConfigError(f"{where} must be an object")
    unknown = set(entry) - _TILE_KEYS
    if unknown:
        raise ConfigError(f"{where} has unknown key(s): {', '.join(sorted(unknown))}")
    name, url = entry.get("name"), entry.get("url")
    if not isinstance(name, str) or not name:
        raise ConfigError(f"{where}.name must be a non-empty string")
    if not isinstance(url, str) or not url.startswith(("http://", "https://", "//")):
        raise ConfigError(f"{where}.url must be an http(s) or protocol-relative (//host/...) URL")
    if not all(token in url for token in ("{z}", "{x}", "{y}")):
        raise ConfigError(f"{where}.url must contain {{z}}, {{x}} and {{y}}")
    out = {"name": name, "url": url, "attribution": entry.get("attribution", ""),
           "max_zoom": entry.get("max_zoom", 19)}
    if not isinstance(out["attribution"], str):
        raise ConfigError(f"{where}.attribution must be a string")
    if not isinstance(out["max_zoom"], int) or not 1 <= out["max_zoom"] <= 24:
        raise ConfigError(f"{where}.max_zoom must be an integer from 1 to 24")
    if "subdomains" in entry:
        if not isinstance(entry["subdomains"], str) or not entry["subdomains"]:
            raise ConfigError(f"{where}.subdomains must be a string such as \"abc\"")
        out["subdomains"] = entry["subdomains"]
    return out


def _parse_map(data) -> tuple[list[dict], str | None]:
    if data is None:
        default = DEFAULT_CONFIG["map"]
        return copy.deepcopy(default["tile_servers"]), default["default"]
    if not isinstance(data, dict):
        raise ConfigError("'map' must be an object")
    servers_raw = data.get("tile_servers", DEFAULT_CONFIG["map"]["tile_servers"])
    if not isinstance(servers_raw, list):
        raise ConfigError("map.tile_servers must be a list")
    servers = [_validate_tile_server(e, i) for i, e in enumerate(servers_raw)]
    names = [s["name"] for s in servers]
    if len(set(names)) != len(names):
        raise ConfigError("map tile server names must be unique")
    default = data.get("default", names[0] if names else None)
    if servers and default not in names:
        raise ConfigError(f"map.default '{default}' is not one of the tile servers")
    return servers, (default if servers else None)


_NODES_KEYS = {"feeds", "cache_ttl_s", "timeout_s"}
_FEED_KEYS = {"name", "url"}


def _parse_nodes(data) -> tuple[list[dict], float, float | None]:
    if data is None:
        default = DEFAULT_CONFIG["nodes"]
        return copy.deepcopy(default["feeds"]), float(default["cache_ttl_s"]), None
    if not isinstance(data, dict):
        raise ConfigError("'nodes' must be an object")
    unknown = set(data) - _NODES_KEYS
    if unknown:
        raise ConfigError(f"nodes has unknown key(s): {', '.join(sorted(unknown))}")
    feeds_raw = data.get("feeds", DEFAULT_CONFIG["nodes"]["feeds"])
    if not isinstance(feeds_raw, list):
        raise ConfigError("nodes.feeds must be a list")
    feeds = []
    for i, entry in enumerate(feeds_raw):
        where = f"nodes.feeds[{i}]"
        if not isinstance(entry, dict):
            raise ConfigError(f"{where} must be an object")
        unknown = set(entry) - _FEED_KEYS
        if unknown:
            raise ConfigError(f"{where} has unknown key(s): {', '.join(sorted(unknown))}")
        name, url = entry.get("name"), entry.get("url")
        if not isinstance(name, str) or not name:
            raise ConfigError(f"{where}.name must be a non-empty string")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ConfigError(f"{where}.url must be an http(s) URL")
        feeds.append({"name": name, "url": url})
    names = [f["name"] for f in feeds]
    if len(set(names)) != len(names):
        raise ConfigError("node feed names must be unique")
    ttl = data.get("cache_ttl_s", DEFAULT_CONFIG["nodes"]["cache_ttl_s"])
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl < 0:
        raise ConfigError("nodes.cache_ttl_s must be a non-negative number")
    timeout = data.get("timeout_s")
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0):
        raise ConfigError("nodes.timeout_s must be a positive number")
    return feeds, float(ttl), (float(timeout) if timeout is not None else None)


def _validate_source(entry, index: int) -> dict:
    if not isinstance(entry, dict):
        raise ConfigError(f"elevation.chain[{index}] must be an object")
    stype = entry.get("type")
    if stype not in SOURCE_TYPES:
        raise ConfigError(f"elevation.chain[{index}].type must be one of {', '.join(SOURCE_TYPES)}")
    out = dict(entry)
    out.setdefault("name", stype)
    if not isinstance(out["name"], str) or not out["name"]:
        raise ConfigError(f"elevation.chain[{index}].name must be a non-empty string")
    if stype == "local_hgt" and not isinstance(out.get("path"), str):
        raise ConfigError(f"elevation.chain[{index}] (local_hgt) needs a 'path'")
    if "url" in out and not (isinstance(out["url"], str) and out["url"].startswith(("http://", "https://"))):
        raise ConfigError(f"elevation.chain[{index}].url must be an http(s) URL")
    for key in ("batch",):
        if key in out and (not isinstance(out[key], int) or out[key] < 1):
            raise ConfigError(f"elevation.chain[{index}].{key} must be a positive integer")
    if "min_interval_s" in out and (not isinstance(out["min_interval_s"], (int, float)) or out["min_interval_s"] < 0):
        raise ConfigError(f"elevation.chain[{index}].min_interval_s must be a non-negative number")
    return out


def parse_config(data: dict, path: str | None = None) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("config must be a JSON object")
    elev = data.get("elevation", {})
    if not isinstance(elev, dict):
        raise ConfigError("'elevation' must be an object")
    chain_raw = elev.get("chain", DEFAULT_CONFIG["elevation"]["chain"])
    if not isinstance(chain_raw, list) or not chain_raw:
        raise ConfigError("elevation.chain must be a non-empty list")
    chain = [_validate_source(e, i) for i, e in enumerate(chain_raw)]
    names = [s["name"] for s in chain]
    if len(set(names)) != len(names):
        raise ConfigError("elevation source names must be unique")
    timeout = elev.get("timeout_s", 10)
    retries = elev.get("retries", 2)
    cache_dir = elev.get("cache_dir")
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ConfigError("elevation.timeout_s must be a positive number")
    if not isinstance(retries, int) or retries < 0:
        raise ConfigError("elevation.retries must be a non-negative integer")
    if cache_dir is not None and not isinstance(cache_dir, str):
        raise ConfigError("elevation.cache_dir must be a string or null")
    tile_servers, default_tiles = _parse_map(data.get("map"))
    feeds, nodes_ttl, nodes_timeout = _parse_nodes(data.get("nodes"))
    return Config(chain, float(timeout), retries, cache_dir, path, tile_servers, default_tiles,
                  feeds, nodes_ttl, nodes_timeout)


def load_config(path: str | None = None, env=None) -> Config:
    """Load the config file, falling back to the built-in internet defaults if it doesn't exist."""
    env = os.environ if env is None else env
    explicit = path or env.get("FRESNEL_CONFIG")
    target = explicit or DEFAULT_CONFIG_PATH
    if not os.path.exists(target):
        if explicit:
            raise ConfigError(f"config file {target} not found")
        return parse_config(copy.deepcopy(DEFAULT_CONFIG))
    try:
        with open(target, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ConfigError(f"can't read config file {target}: {exc}") from exc
    return parse_config(data, target)


def make_provider(spec: dict, timeout_s: float = 10.0, env=None):
    kwargs = {k: v for k, v in spec.items() if k not in ("name", "type")}
    stype, name = spec["type"], spec["name"]
    if stype == "local_hgt":
        return LocalHgtProvider(name, kwargs["path"])
    kwargs.setdefault("timeout_s", timeout_s)
    if stype == "opentopodata":
        allowed = {"url", "datasets", "batch", "min_interval_s", "timeout_s"}
        return OpenTopoDataProvider(name, **{k: v for k, v in kwargs.items() if k in allowed})
    if stype == "open-elevation":
        allowed = {"url", "batch", "min_interval_s", "timeout_s"}
        return OpenElevationProvider(name, **{k: v for k, v in kwargs.items() if k in allowed})
    if stype == "usgs":
        allowed = {"url", "min_interval_s", "timeout_s"}
        return USGSProvider(name, **{k: v for k, v in kwargs.items() if k in allowed})
    if stype == "google":
        allowed = {"url", "key_env", "key", "batch", "min_interval_s", "timeout_s"}
        return GoogleProvider(name, env=env, **{k: v for k, v in kwargs.items() if k in allowed})
    raise ConfigError(f"unknown source type {stype}")


def build_chain(config: Config, only: str | None = None, env=None) -> ElevationChain:
    specs = config.chain
    if only and only != "auto":
        specs = [s for s in specs if s["name"] == only]
        if not specs:
            raise KeyError(only)
    providers = [make_provider(s, config.timeout_s, env) for s in specs]
    return ElevationChain(providers, retries=config.retries, cache=open_cache(config.cache_dir))
