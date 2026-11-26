"""Command-line interface and Uvicorn runner for Miele Local."""

import argparse
import asyncio
import logging
import logging.handlers
import os
import signal
import sys
from pathlib import Path
from typing import Optional

import uvicorn

from .api import MieleLocalAPI
from .routes import create_app, register_routes
from .zeroconf_register import get_primary_ipv4

logger = logging.getLogger(__name__)

miele_api: Optional[MieleLocalAPI] = None
server: Optional[uvicorn.Server] = None
shutdown_event: Optional[asyncio.Event] = None


async def run_server(args: argparse.Namespace) -> None:
    """Initialize database, import optional YAML config, and run FastAPI + Uvicorn."""
    global miele_api, server, shutdown_event

    shutdown_event = asyncio.Event()

    def handle_signal(signum, _frame):
        logger.info("Received signal %s, initiating shutdown...", signum)
        shutdown_event.set()
        if miele_api:
            for listeners in (miele_api.event_listeners, miele_api.device_event_listeners):
                for queue in list(listeners):
                    try:
                        queue.put_nowait(None)
                    except Exception:
                        pass
        if server:
            server.should_exit = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        db_path = Path(os.path.expanduser(args.state))
        db_path.parent.mkdir(parents=True, exist_ok=True)

        miele_api = MieleLocalAPI(str(db_path), purge_history_days=args.purgehistory)

        if args.config and Path(args.config).is_file():
            try:
                imported = await miele_api.import_yaml_file(args.config)
                logger.info("Imported %d appliance(s) from %s", imported, args.config)
            except Exception as exc:
                logger.warning("Failed to import YAML config %s: %s", args.config, exc)

        app = create_app()
        register_routes(app, lambda: miele_api)

        await miele_api.start()

        if not args.no_mdns:
            try:
                from .__version__ import __version__ as miele_version
                from .zeroconf_register import register_service_async

                mdns_port = int(
                    os.getenv("MIELE_EXTERNAL_PORT")
                    or os.getenv("EXTERNAL_PORT")
                    or args.port
                )
                asyncio.create_task(
                    register_service_async(
                        name="miele-local",
                        port=mdns_port,
                        props={
                            "path": "/",
                            "version": miele_version,
                            "app": "miele-local",
                            "id": "miele-local",
                        },
                        service_type="_http._tcp.local.",
                    )
                )
            except Exception as exc:
                logger.warning("mDNS registration unavailable: %s", exc)

        server_ip = get_primary_ipv4() or "0.0.0.0"
        logger.info("*** Miele Local ready! ***")
        logger.info("Web UI:        http://%s:%s/", server_ip, args.port)
        logger.info("API Docs:      http://%s:%s/docs", server_ip, args.port)
        logger.info("Devices API:   http://%s:%s/devices", server_ip, args.port)
        logger.info("Live Events:   http://%s:%s/events", server_ip, args.port)

        config = uvicorn.Config(
            app,
            host=args.bind,
            port=args.port,
            access_log=True,
        )
        server = uvicorn.Server(config)
        await server.serve()
    finally:
        if miele_api:
            await miele_api.cleanup()
        try:
            from .zeroconf_register import unregister_service

            unregister_service()
        except Exception:
            pass
        if args.pid_file:
            pid_path = Path(args.pid_file)
            if pid_path.exists():
                try:
                    pid_path.unlink()
                except Exception:
                    pass


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="miele-local",
        description="Miele Local - Local REST API & Web UI for Miele@home appliances",
    )
    parser.add_argument(
        "--state",
        default="~/.miele-local.db",
        help="Path to SQLite state database (default: ~/.miele-local.db)",
    )
    parser.add_argument(
        "-c",
        "--config",
        default=os.getenv("MIELE_CONFIG", ""),
        help="Optional MieleRESTServer YAML config file to import at startup",
    )
    parser.add_argument(
        "-b",
        "--bind",
        default="0.0.0.0",
        help="IP address to bind to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=5001,
        help="Port for REST API and Web UI (default: 5001)",
    )
    parser.add_argument(
        "--no-mdns",
        action="store_true",
        help="Disable mDNS/zeroconf service advertisement",
    )
    parser.add_argument(
        "--purgehistory",
        type=int,
        default=30,
        help="Automatically delete history records older than N days (default: 30, 0 disables)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable DEBUG logging")
    parser.add_argument("--daemon", action="store_true", help="Structured daemon logging")
    parser.add_argument("--syslog", help="Send logs to syslog (/dev/log or host:port)")
    parser.add_argument("--pid-file", help="Write process ID to specified file")

    args = parser.parse_args()

    if args.syslog:
        addr = args.syslog
        if ":" in addr and not addr.startswith("/"):
            host, port_str = addr.rsplit(":", 1)
            addr = (host, int(port_str))
        handler = logging.handlers.SysLogHandler(
            address=addr, facility=logging.handlers.SysLogHandler.LOG_DAEMON
        )
        handler.setFormatter(logging.Formatter("miele-local[%(process)d]: %(levelname)s %(message)s"))
        logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    elif args.daemon:
        logging.basicConfig(
            level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout, force=True
        )
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            stream=sys.stdout,
            force=True,
        )

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.pid_file:
        Path(args.pid_file).write_text(str(os.getpid()), encoding="utf-8")

    try:
        asyncio.run(run_server(args))
    except KeyboardInterrupt:
        logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
