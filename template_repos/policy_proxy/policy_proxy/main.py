"""Uvicorn entrypoint for the per-app policy-proxy sidecar."""

import logging

import uvicorn

from . import app as app_mod
from . import config as config_mod


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("policy_proxy.main")
    proxy_cfg = config_mod.load_proxy_config_from_env()
    logger.info(
        "policy-proxy starting listen_port=%d app=%s env=%s env_domain=%s upstream=%s:%d",
        proxy_cfg.listen_port, proxy_cfg.app_id, proxy_cfg.env_slug,
        proxy_cfg.env_domain, proxy_cfg.upstream_host, proxy_cfg.upstream_port,
    )
    fastapi_app = app_mod.create_app(cfg=proxy_cfg)
    uvicorn.run(
        fastapi_app,
        host="0.0.0.0",
        port=proxy_cfg.listen_port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
