"""Run the app: python -m multisport [--port 8081] [--data DIR]"""

import argparse
import logging
import logging.handlers
import os
import socket
from pathlib import Path

from .app import create_app, default_data_dir


def _setup_logging(data_dir):
    log_dir = Path(data_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(log_dir / "app.log", maxBytes=1_000_000, backupCount=3)
    console = logging.StreamHandler()
    for handler in (file_handler, console):
        handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console)
    logging.getLogger("urllib3").setLevel(logging.WARNING)  # its debug lines would include the API token


def main():
    parser = argparse.ArgumentParser(prog="multisport", description="Multisport Helper web app")
    parser.add_argument("--host", default=os.environ.get("MULTISPORT_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MULTISPORT_PORT", "8081")))
    parser.add_argument("--data", default=str(default_data_dir()), help="folder for settings and logs")
    args = parser.parse_args()

    _setup_logging(args.data)
    app = create_app(args.data)
    logging.getLogger("multisport").info("Open http://%s.local:%s in a browser", socket.gethostname(), args.port)

    from waitress import serve

    serve(app, host=args.host, port=args.port, threads=4, ident="MultisportHelper")


if __name__ == "__main__":
    main()
