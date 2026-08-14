from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

from core import __version__
from core.config import ConfigError, load_config
from core.mirror import RepositorySynchronizer

LOG = logging.getLogger("reposync")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reposync", description="Force-mirror Git repositories from source to target"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("validate", "sync", "run"):
        command = subparsers.add_parser(name)
        command.add_argument("--config", type=Path, default=Path("/etc/reposync/config.yml"))
        if name == "sync":
            command.add_argument("--dry-run", action="store_true")
    return parser


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


def _sync_once(config_path: Path, *, dry_run: bool = False) -> int:
    config = load_config(config_path)
    with RepositorySynchronizer(config) as synchronizer:
        results = synchronizer.sync_all(dry_run=dry_run)
    changed = sum(result.changed for result in results)
    LOG.info("sync cycle complete repositories=%d changed=%d", len(results), changed)
    return 0


def _run(config_path: Path) -> int:
    stop = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        LOG.info("received signal=%d; stopping after the current operation", signum)
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    while not stop.is_set():
        try:
            config = load_config(config_path)
        except ConfigError as exc:
            LOG.error("%s", exc)
            return 2

        try:
            with RepositorySynchronizer(config) as synchronizer:
                synchronizer.sync_all()
        except Exception:
            LOG.exception("sync cycle failed")
        if not stop.is_set():
            LOG.info("next sync in %ds", config.interval_seconds)
        stop.wait(config.interval_seconds)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    _configure_logging(args.log_level)
    try:
        if args.command == "validate":
            config = load_config(args.config)
            print(f"configuration valid: {len(config.repositories)} repository job(s)")
            return 0
        if args.command == "sync":
            return _sync_once(args.config, dry_run=args.dry_run)
        return _run(args.config)
    except (ConfigError, RuntimeError, OSError) as exc:
        LOG.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
