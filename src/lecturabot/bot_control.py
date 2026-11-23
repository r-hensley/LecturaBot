"""Inspect or reload LecturaBot through its private Unix socket."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import socket
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path


LOGGER = logging.getLogger(__name__)
MAX_REQUEST = 1024


class BotControl:
    def __init__(
        self,
        path: Path,
        *,
        features_loaded: Callable[[], bool],
        reload_features: Callable[[], Awaitable[None]],
    ) -> None:
        self.path = path.expanduser()
        self.features_loaded = features_loaded
        self.reload_features = reload_features
        self.server: asyncio.Server | None = None
        self._bound_file: os.stat_result | None = None

    async def start(self) -> None:
        if not self.path.is_absolute():
            raise ValueError("The control socket path must be absolute.")
        self.path.parent.mkdir(mode=0o700, exist_ok=True)
        parent = self.path.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
                or stat.S_IMODE(parent.st_mode) != 0o700):
            raise PermissionError("The socket directory must be owned by the bot user with mode 0700.")

        # Binding first prevents a second process from replacing a live socket.
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self.path))
            self._bound_file = self.path.lstat()
            self.path.chmod(0o600)
            self.server = await asyncio.start_unix_server(
                self._handle, sock=listener, limit=MAX_REQUEST
            )
        except BaseException:
            listener.close()
            self._remove_socket()
            raise

    def _remove_socket(self) -> None:
        bound_file, self._bound_file = self._bound_file, None
        if bound_file is not None:
            try:
                if os.path.samestat(bound_file, self.path.lstat()):
                    self.path.unlink()
            except FileNotFoundError:
                pass

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        self._remove_socket()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=5)
                if not line.endswith(b"\n") or len(line) > MAX_REQUEST:
                    raise ValueError("Incomplete or oversized request.")
                request = json.loads(line)
            except (ValueError, TimeoutError):
                result = {"ok": False, "error": "Expected one JSON line under 1 KiB."}
            else:
                result = await self._execute(request)
            writer.write(json.dumps(result).encode() + b"\n")
            await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    async def _execute(self, request: object) -> dict:
        if request == {"action": "status", "targets": []}:
            return {"ok": True, "loaded": ["features"] if self.features_loaded() else []}
        if request != {"action": "reload", "targets": ["features"]}:
            return {"ok": False, "error": "Expected status or reload features."}
        try:
            await self.reload_features()
        except Exception as error:
            LOGGER.exception("Local features reload failed")
            return {"ok": False, "error": type(error).__name__}
        return {"ok": True, "completed": ["features"]}


async def send_request(path: Path, action: str, targets: list[str]) -> dict:
    reader, writer = await asyncio.open_unix_connection(str(path.expanduser()))
    try:
        writer.write(json.dumps({"action": action, "targets": targets}).encode() + b"\n")
        await writer.drain()
        return json.loads(await asyncio.wait_for(reader.readline(), timeout=60))
    finally:
        writer.close()
        await writer.wait_closed()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("action", choices=("status", "reload"))
    parser.add_argument("targets", nargs="*")
    args = parser.parse_args()
    if args.targets != (["features"] if args.action == "reload" else []):
        parser.error("Use status or reload features.")
    try:
        result = asyncio.run(send_request(args.socket, args.action, args.targets))
    except TimeoutError:
        parser.exit(1, "Bot control timed out. Check the service log before retrying.\n")
    except (OSError, ValueError) as error:
        parser.exit(1, f"Bot control request failed: {error}\n")
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
