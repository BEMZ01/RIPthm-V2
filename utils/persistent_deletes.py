import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional

import discord


class PersistentDeleteQueue:
    """Persist `delete_after` operations so they can continue after a restart."""

    def __init__(self, bot, file_path: str, logger, poll_interval: float = 2.0):
        self.bot = bot
        self.file_path = file_path
        self.logger = logger
        self.poll_interval = max(1.0, float(poll_interval))
        self._lock = asyncio.Lock()
        self._worker: Optional[asyncio.Task] = None

    async def start(self):
        if self._worker is not None and not self._worker.done():
            return
        self._worker = asyncio.create_task(self._run(), name="persistent-delete-worker")

    async def stop(self):
        if self._worker is None:
            return
        self._worker.cancel()
        try:
            await self._worker
        except asyncio.CancelledError:
            pass
        finally:
            self._worker = None

    async def schedule(self, message: Any, delay_seconds: Any):
        if message is None or delay_seconds is None:
            return

        try:
            delay_seconds = float(delay_seconds)
        except (TypeError, ValueError):
            return

        if delay_seconds <= 0:
            return

        message_id = getattr(message, "id", None)
        channel = getattr(message, "channel", None)
        channel_id = getattr(channel, "id", None)
        if message_id is None or channel_id is None:
            return

        # Ephemeral responses are not fetchable/deletable as regular messages.
        flags = getattr(message, "flags", None)
        if flags is not None and getattr(flags, "ephemeral", False):
            return

        entry = {
            "message_id": int(message_id),
            "channel_id": int(channel_id),
            "due_at": time.time() + delay_seconds,
        }

        async with self._lock:
            entries = self._read_entries_unlocked()
            entries = [e for e in entries if e.get("message_id") != entry["message_id"]]
            entries.append(entry)
            self._write_entries_unlocked(entries)

    async def _run(self):
        try:
            await self.bot.wait_until_ready()
            while not self.bot.is_closed():
                await self._process_due_entries()
                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.exception(f"Persistent delete worker crashed: {exc}")

    async def _process_due_entries(self):
        now = time.time()
        due: List[Dict[str, Any]] = []

        async with self._lock:
            entries = self._read_entries_unlocked()
            pending: List[Dict[str, Any]] = []
            for entry in entries:
                if float(entry.get("due_at", 0)) <= now:
                    due.append(entry)
                else:
                    pending.append(entry)
            if len(pending) != len(entries):
                self._write_entries_unlocked(pending)

        for entry in due:
            await self._delete_entry(entry)

    async def _delete_entry(self, entry: Dict[str, Any]):
        channel_id = int(entry["channel_id"])
        message_id = int(entry["message_id"])

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.errors.Forbidden, discord.errors.NotFound):
                return
            except discord.errors.HTTPException:
                await self._requeue(entry, 30)
                return

        try:
            message = await channel.fetch_message(message_id)
            await message.delete()
        except discord.errors.NotFound:
            # Already deleted while we were offline or by native delete_after.
            return
        except discord.errors.Forbidden:
            self.logger.warning(
                f"Cannot delete message {message_id} in channel {channel_id} due to missing permissions."
            )
        except discord.errors.HTTPException:
            await self._requeue(entry, 30)

    async def _requeue(self, entry: Dict[str, Any], delay_seconds: float):
        retry_entry = dict(entry)
        retry_entry["due_at"] = time.time() + delay_seconds
        async with self._lock:
            entries = self._read_entries_unlocked()
            entries = [e for e in entries if e.get("message_id") != retry_entry["message_id"]]
            entries.append(retry_entry)
            self._write_entries_unlocked(entries)

    def _read_entries_unlocked(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.file_path):
            return []
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        except (OSError, json.JSONDecodeError):
            self.logger.warning(f"Failed to read persistent delete file {self.file_path}; starting fresh.")
        return []

    def _write_entries_unlocked(self, entries: List[Dict[str, Any]]):
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        temp_path = f"{self.file_path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(entries, f)
        os.replace(temp_path, self.file_path)

