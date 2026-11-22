"""Entry point: python -m controller"""

import logging
import os
import threading
import time

from .adapters import Alerter, DockerCLI, HttpGateway, HttpProber, NullGateway
from .api import make_server
from .config import Config, parse_servers
from .core import Controller
from .events import EventLog


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log = logging.getLogger("controller")

    cfg = Config.from_env()
    servers = parse_servers(os.getenv(
        "SERVERS", "app1=http://app1:8000,app2=http://app2:8000,app3=http://app3:8000"))
    data_dir = os.getenv("DATA_DIR", "/data")
    gateway_url = os.getenv("GATEWAY_URL", "")

    events = EventLog(f"{data_dir}/events.jsonl")
    controller = Controller(
        cfg=cfg,
        servers=servers,
        prober=HttpProber(cfg.probe_timeout_s),
        docker=DockerCLI(),
        gateway=HttpGateway(gateway_url) if gateway_url else NullGateway(),
        events=events,
        alerter=Alerter(os.getenv("ALERT_WEBHOOK_URL", "")),
    )

    port = int(os.getenv("PORT", "9100"))
    httpd = make_server(controller, port=port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    log.info("controller up: servers=%s gateway=%s api=:%d", list(servers), gateway_url or "none", port)
    events.emit("controller_started", servers=list(servers))

    while True:
        t0 = time.monotonic()
        try:
            controller.tick()
        except Exception:
            log.exception("tick failed")  # never let one bad tick kill the controller
        time.sleep(max(0.0, cfg.probe_interval_s - (time.monotonic() - t0)))


if __name__ == "__main__":
    main()
