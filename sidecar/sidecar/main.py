"""Uvicorn entrypoint for the sidecar."""

import logging

import uvicorn

from . import app as app_mod
from . import config as config_mod


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = config_mod.load_config_from_env()
    fastapi_app = app_mod.create_app(cfg=cfg)
    uvicorn.run(
        fastapi_app,
        host="0.0.0.0",
        port=cfg.listen_port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
