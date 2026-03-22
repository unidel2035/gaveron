"""Gaveron entry point — run with: python -m gaveron"""

import asyncio
import argparse
import logging
import signal
import sys
from pathlib import Path

from .config import Config
from .decoder import AircraftStore
from .feed import BeastFeed, SBSFeed, JSONFileFeed
from .history import HistoryManager
from .server import GaveronServer
from .trackdb import TrackDB


def parse_args():
    parser = argparse.ArgumentParser(
        description="Gaveron — ADS-B aircraft tracking server"
    )
    parser.add_argument(
        "--config", "-c",
        help="Path to config YAML file",
        default=None,
    )
    parser.add_argument(
        "--feed-type",
        choices=["beast", "sbs", "json_file"],
        help="Data feed type (default: beast)",
    )
    parser.add_argument(
        "--feed-host",
        help="Feed source host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--feed-port",
        type=int,
        help="Feed source port (default: 30005 for beast, 30003 for sbs)",
    )
    parser.add_argument(
        "--json-path",
        help="Path to aircraft.json (for json_file feed type)",
    )
    parser.add_argument(
        "--http-port",
        type=int,
        help="HTTP server port (default: 8080)",
    )
    parser.add_argument(
        "--lat",
        type=float,
        help="Receiver latitude",
    )
    parser.add_argument(
        "--lon",
        type=float,
        help="Receiver longitude",
    )
    parser.add_argument(
        "--history-dir",
        help="Directory for history chunks (default: /run/gaveron)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Load config: file -> env -> defaults
    if args.config:
        config = Config.from_file(args.config)
    else:
        config = Config.from_env()

    # CLI overrides
    if args.feed_type:
        config.feed_type = args.feed_type
    if args.feed_host:
        config.feed_host = args.feed_host
    if args.feed_port:
        config.feed_port = args.feed_port
    if args.json_path:
        config.json_file_path = args.json_path
    if args.http_port:
        config.http_port = args.http_port
    if args.lat is not None:
        config.receiver_lat = args.lat
    if args.lon is not None:
        config.receiver_lon = args.lon
    if args.history_dir:
        config.history_dir = args.history_dir
    if args.log_level:
        config.log_level = args.log_level

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, config.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("gaveron")

    logger.info("Gaveron ADS-B server starting...")
    logger.info("Feed: %s @ %s:%d", config.feed_type, config.feed_host, config.feed_port)
    logger.info("HTTP: %s:%d", config.http_host, config.http_port)
    logger.info("History: %s (interval=%.1fs, size=%d)",
                config.history_dir, config.history_interval, config.history_size)

    # Create components
    store = AircraftStore(timeout=config.aircraft_timeout)

    # Track database
    db_path = str(Path(config.history_dir) / "gaveron_tracks.db")
    trackdb = TrackDB(db_path=db_path, retention_hours=config.track_retention_hours)

    # Select feed
    if config.feed_type == "beast":
        feed = BeastFeed(store, config.feed_host, config.feed_port)
    elif config.feed_type == "sbs":
        feed = SBSFeed(store, config.feed_host, config.feed_port)
    elif config.feed_type == "json_file":
        feed = JSONFileFeed(store, config.json_file_path)
    else:
        logger.error("Unknown feed type: %s", config.feed_type)
        sys.exit(1)

    history = HistoryManager(
        store,
        output_dir=config.history_dir,
        interval=config.history_interval,
        history_size=config.history_size,
        chunk_size=config.chunk_size,
        trackdb=trackdb,
    )

    server = GaveronServer(
        store,
        trackdb=trackdb,
        history_dir=config.history_dir,
        host=config.http_host,
        port=config.http_port,
        receiver_lat=config.receiver_lat,
        receiver_lon=config.receiver_lon,
    )

    # Run
    loop = asyncio.new_event_loop()

    def shutdown(sig, frame):
        logger.info("Shutting down...")
        feed.stop()
        history.stop()
        loop.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    async def run_all():
        await asyncio.gather(
            feed.start(),
            history.start(),
            server.start(),
        )

    try:
        loop.run_until_complete(run_all())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
        logger.info("Gaveron stopped.")


if __name__ == "__main__":
    main()
