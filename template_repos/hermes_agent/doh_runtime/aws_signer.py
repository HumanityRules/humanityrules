"""DOH streaming SigV4 proxy — replaces aws-sigv4-proxy + haproxy.

Binds 127.0.0.1:9901/9902/9903 (STS / Bedrock / Bedrock-runtime), signs each
inbound request with the ECS task role credentials, and streams request and
response bodies through without buffering. Started by supervisor.sh outside
the nono sandbox so credentials never enter the Hermes process environment.

The upstream aws-sigv4-proxy image buffers the full response body before
flushing, which breaks Bedrock event-stream responses (and SSE in general);
see awslabs/aws-sigv4-proxy#250. This replacement streams via urllib3's
preload_content=False so bytes go out as fast as Bedrock produces them.

Limitations:
- Request bodies are fully read before signing (SigV4 needs the payload
  hash). Fine for Bedrock; S3 large uploads would need aws-chunked signing.
- HTTP/1.0 on the loopback side — response ends at connection close, so
  upstream Transfer-Encoding: chunked framing doesn't need to be re-emitted.
"""

import argparse
import logging
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import urllib3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session

logger = logging.getLogger("aws-signer")

_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}
# Dropped from the client request before re-signing. The sandboxed client
# signs with dummy creds (see /workspace/.aws/credentials), so its headers
# must not leak into the outbound request we sign with real creds.
_STRIP_BEFORE_SIGN = _HOP_BY_HOP | {
    "host",
    "authorization",
    "x-amz-date",
    "x-amz-content-sha256",
    "x-amz-security-token",
}


@dataclass(frozen=True)
class PortConfig:
    port: int
    service: str
    region: str
    upstream_host: str


def _reload_session_credentials(session: Session) -> None:
    # Shared-credentials and env-based providers cache on the session.
    # Clearing forces botocore to re-read ~/.aws on the next get_credentials().
    session._credentials = None


def _upstream_expired_token(upstream: urllib3.HTTPResponse) -> bool:
    error_type = upstream.headers.get("x-amzn-errortype", "")
    return error_type.startswith("ExpiredTokenException")


def _build_handler(cfg: PortConfig, session: Session, pool: urllib3.HTTPSConnectionPool):
    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.0: response ends at connection close, so we don't have to
        # re-frame upstream's Transfer-Encoding: chunked output ourselves.
        protocol_version = "HTTP/1.0"

        def log_message(self, fmt, *args):
            return

        def _proxy(self):
            content_length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(content_length) if content_length else b""

            outbound_headers = {
                k: v for k, v in self.headers.items()
                if k.lower() not in _STRIP_BEFORE_SIGN
            }

            upstream = None
            for attempt in range(2):
                aws_request = AWSRequest(
                    method=self.command,
                    url=f"https://{cfg.upstream_host}{self.path}",
                    data=body,
                    headers=outbound_headers,
                )
                credentials = session.get_credentials()
                if credentials is None:
                    self.send_error(500, "no AWS credentials available")
                    return
                SigV4Auth(
                    credentials.get_frozen_credentials(), cfg.service, cfg.region,
                ).add_auth(aws_request)

                try:
                    upstream = pool.urlopen(
                        method=self.command,
                        url=self.path,
                        body=body,
                        headers=dict(aws_request.headers),
                        preload_content=False,
                        redirect=False,
                        retries=False,
                    )
                except Exception as exc:
                    logger.error("upstream error port=%d err=%s", cfg.port, exc)
                    try:
                        self.send_error(502, f"upstream unreachable: {exc}")
                    except Exception:
                        pass
                    return

                if (
                    upstream.status == 403
                    and _upstream_expired_token(upstream)
                    and attempt == 0
                ):
                    upstream.drain_conn()
                    upstream.release_conn()
                    logger.info(
                        "expired AWS credentials on port=%d; reloading and retrying once",
                        cfg.port,
                    )
                    _reload_session_credentials(session)
                    continue
                break

            try:
                self.send_response(upstream.status)
                for name, value in upstream.headers.items():
                    if name.lower() not in _HOP_BY_HOP:
                        self.send_header(name, value)
                self.end_headers()
                for chunk in upstream.stream(4096, decode_content=False):
                    if not chunk:
                        continue
                    self.wfile.write(chunk)
                    self.wfile.flush()
            finally:
                upstream.release_conn()

        do_GET = _proxy
        do_POST = _proxy
        do_PUT = _proxy
        do_DELETE = _proxy
        do_HEAD = _proxy
        do_PATCH = _proxy

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [aws-signer] %(message)s",
    )

    session = Session()
    if session.get_credentials() is None:
        raise SystemExit("no AWS credentials available — aws-signer requires a task role")

    # SigV4 signing_name != endpoint service name for Bedrock: both
    # bedrock.* and bedrock-runtime.* are scoped to "bedrock" in the
    # credential-scope string.
    configs = [
        PortConfig(9901, "sts",     args.region, "sts.amazonaws.com"),
        PortConfig(9902, "bedrock", args.region, f"bedrock.{args.region}.amazonaws.com"),
        PortConfig(9903, "bedrock", args.region, f"bedrock-runtime.{args.region}.amazonaws.com"),
    ]

    for cfg in configs:
        # maxsize=16: typical Hermes burst is ~3 concurrent Bedrock calls (main
        # + aux + a tool-side aux). Idle conns auto-close; the pool itself
        # lives forever alongside the server thread.
        pool = urllib3.HTTPSConnectionPool(
            cfg.upstream_host, port=443, maxsize=16, block=False, timeout=300,
        )
        handler_cls = _build_handler(cfg, session, pool)
        server = ThreadingHTTPServer(("127.0.0.1", cfg.port), handler_cls)
        threading.Thread(
            target=server.serve_forever, name=f"signer-{cfg.port}", daemon=True,
        ).start()
        logger.info("listening on 127.0.0.1:%d -> %s (service=%s)",
                    cfg.port, cfg.upstream_host, cfg.service)

    threading.Event().wait()


if __name__ == "__main__":
    main()
