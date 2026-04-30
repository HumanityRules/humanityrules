"""Uvicorn entrypoint for the policy proxy. Dispatches on DOH_ROLE."""

import logging

import uvicorn

from . import app as app_mod
from . import auth as auth_mod
from . import config as config_mod


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    role = config_mod.role_from_env()
    if role == config_mod.ROLE_AUTH:
        auth_cfg = config_mod.load_auth_config_from_env()
        fastapi_app = app_mod.create_auth_app(
            cfg=auth_cfg, secrets_client=auth_mod.new_secrets_client(),
        )
        listen_port = auth_cfg.listen_port
    else:
        proxy_cfg = config_mod.load_proxy_config_from_env()
        fastapi_app = app_mod.create_app(cfg=proxy_cfg)
        listen_port = proxy_cfg.listen_port

    uvicorn.run(
        fastapi_app,
        host="0.0.0.0",
        port=listen_port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
