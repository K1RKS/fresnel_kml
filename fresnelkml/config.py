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
    }
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

    def source_names(self) -> list[str]:
        return [s["name"] for s in self.chain]

    def public_sources(self) -> list[dict]:
        return [{"name": s["name"], "type": s["type"]} for s in self.chain]


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
    return Config(chain, float(timeout), retries, cache_dir, path)


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
