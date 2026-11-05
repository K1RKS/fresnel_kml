#!/usr/bin/env python3
"""Print the NGINX settings that cache map tiles for the tile servers with a cache_mb in the config."""

from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fresnelkml.cache import slug  # noqa: E402
from fresnelkml.config import ConfigError, load_config, tile_proxy  # noqa: E402
from fresnelkml.httpjson import USER_AGENT  # noqa: E402

DEFAULT_CACHE_ROOT = "/var/cache/nginx/fresnelkml"


def zone_name(name: str) -> str:
    return "fresnel_" + slug(name).replace("-", "_")


def render(config, site: str, cache_root: str = DEFAULT_CACHE_ROOT) -> str:
    """The http {} and server {} parts for every cached tile server, or a note when there are none."""
    site = "/" + site.strip("/") + "/" if site.strip("/") else "/"
    cached = [s for s in config.tile_servers if s.get("cache_mb")]
    if not cached:
        return "# No tile server in the config has a cache_mb, so there is nothing to cache.\n"
    http, server = [], []
    for s in cached:
        upstream, _ = tile_proxy(s)
        host = urlsplit(upstream).hostname
        zone = zone_name(s["name"])
        http.append(f"proxy_cache_path {cache_root}/{slug(s['name'])} levels=1:2 keys_zone={zone}:10m "
                    f"max_size={int(s['cache_mb'])}m inactive=30d use_temp_path=off;")
        server += [
            f"# {s['name']}: up to {int(s['cache_mb'])} MB",
            f"location ^~ {site}tiles/{slug(s['name'])}/ {{",
            "    limit_except GET HEAD { deny all; }",
            f"    proxy_pass {upstream};",
            f"    proxy_cache {zone};",
            "    proxy_cache_valid 200 7d;",
            "    proxy_cache_valid 404 1h;",
            "    proxy_cache_use_stale error timeout updating http_500 http_502 http_503 http_504;",
            "    proxy_cache_lock on;",
            "    proxy_http_version 1.1;",
            "    proxy_ssl_server_name on;",
            f"    proxy_set_header Host {host};",
            f'    proxy_set_header User-Agent "{USER_AGENT}";',
            '    proxy_set_header Cookie "";',
            "    proxy_ignore_headers Set-Cookie;",
            "    proxy_hide_header Set-Cookie;",
            "    add_header X-Cache-Status $upstream_cache_status always;",
            "}",
            "",
        ]
    return "\n".join([
        "# ---- In the http { } block (for example /etc/nginx/conf.d/fresnelkml-tiles.conf) ----",
        *http,
        "",
        f"# ---- In the server {{ }} block that serves {site} ----",
        *server,
    ])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", help="server config file (default: FRESNEL_CONFIG or /etc/fresnelkml/config.json)")
    p.add_argument("--site", required=True, help="the site's URL path, such as /fresnel_kml/")
    p.add_argument("--cache-root", default=DEFAULT_CACHE_ROOT,
                   help=f"folder for NGINX's tile caches (default {DEFAULT_CACHE_ROOT})")
    args = p.parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(render(config, args.site, args.cache_root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
