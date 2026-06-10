import os
import sys

sys.path.insert(0, "/opt/doh/runtime/webapps")

import uvicorn

from admin.server import app

uvicorn.run(
    app,
    host="127.0.0.1",
    port=int(os.environ["WEBAPP_PORT"]),
    log_level="info",
)
