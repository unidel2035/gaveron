"""History manager — maintains rolling aircraft track history.

Similar to tar1090.sh but in Python: creates periodic snapshots,
compresses them into chunks, and maintains an index.
"""

import gzip
import json
import logging
import time
from collections import deque
from pathlib import Path
from typing import Optional

from .decoder import AircraftStore

logger = logging.getLogger("gaveron.history")


class HistoryManager:
    """Manages rolling history of aircraft snapshots."""

    def __init__(
        self,
        store: AircraftStore,
        output_dir: str = "/run/gaveron",
        interval: float = 8.0,
        history_size: int = 450,
        chunk_size: int = 20,
    ):
        self.store = store
        self.output_dir = Path(output_dir)
        self.interval = interval
        self.history_size = history_size
        self.chunk_size = chunk_size

        self._snapshots: deque = deque(maxlen=history_size)
        self._chunks: list[str] = []
        self._running = False

    def ensure_dirs(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def start(self):
        import asyncio

        self._running = True
        self.ensure_dirs()
        logger.info(
            "History manager started: dir=%s, interval=%.1fs, "
            "history_size=%d, chunk_size=%d",
            self.output_dir, self.interval,
            self.history_size, self.chunk_size,
        )

        while self._running:
            try:
                self._take_snapshot()
                self._maybe_write_chunk()
                self._write_current()
                self._write_chunks_index()
            except Exception as e:
                logger.error("History error: %s", e)
            await asyncio.sleep(self.interval)

    def _take_snapshot(self):
        """Take a snapshot of current aircraft state."""
        data = self.store.to_json()
        # Compact format: only essential fields
        compact = []
        for ac in data["aircraft"]:
            entry = [
                ac["hex"],
                ac.get("alt_baro"),
                ac.get("gs"),
                ac.get("track"),
                ac.get("lat"),
                ac.get("lon"),
                ac.get("seen_pos"),
                ac.get("type", ""),
                ac.get("flight", ""),
                ac.get("messages", 0),
            ]
            compact.append(entry)

        snapshot = {
            "now": data["now"],
            "aircraft": compact,
        }
        self._snapshots.append(snapshot)

    def _maybe_write_chunk(self):
        """Write a chunk if we have enough snapshots."""
        # Count un-chunked snapshots
        total_chunked = len(self._chunks) * self.chunk_size
        unchunked = len(self._snapshots) - total_chunked

        if unchunked >= self.chunk_size:
            # Write a new chunk
            start = total_chunked
            end = start + self.chunk_size
            chunk_data = list(self._snapshots)[start:end]

            timestamp = int(time.time())
            chunk_name = f"chunk_{timestamp}.gz"
            chunk_path = self.output_dir / chunk_name

            content = json.dumps(chunk_data, separators=(",", ":"))
            with gzip.open(chunk_path, "wt", compresslevel=1) as f:
                f.write(content)

            self._chunks.append(chunk_name)
            logger.debug("Wrote chunk: %s (%d snapshots)", chunk_name, len(chunk_data))

            # Cleanup old chunks (keep enough for history_size)
            max_chunks = self.history_size // self.chunk_size + 1
            while len(self._chunks) > max_chunks:
                old_chunk = self._chunks.pop(0)
                old_path = self.output_dir / old_chunk
                if old_path.exists():
                    old_path.unlink()
                    logger.debug("Removed old chunk: %s", old_chunk)

    def _write_current(self):
        """Write current (un-chunked) snapshots to current.gz."""
        total_chunked = len(self._chunks) * self.chunk_size
        current_snapshots = list(self._snapshots)[total_chunked:]

        if not current_snapshots:
            return

        path = self.output_dir / "current.gz"
        content = json.dumps(current_snapshots, separators=(",", ":"))
        with gzip.open(path, "wt", compresslevel=1) as f:
            f.write(content)

    def _write_chunks_index(self):
        """Write chunks.json index file."""
        path = self.output_dir / "chunks.json"
        with open(path, "w") as f:
            json.dump({"chunks": self._chunks}, f, separators=(",", ":"))

    def stop(self):
        self._running = False
