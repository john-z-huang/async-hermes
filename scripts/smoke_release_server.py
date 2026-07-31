#!/usr/bin/env python3
"""验证已冻结的 Hermes Server 可启动并输出完整版本握手。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import queue
import subprocess
import threading


def _read_line(stream, result: queue.Queue[str]) -> None:
    result.put(stream.readline())


def main() -> None:
    parser = argparse.ArgumentParser(description="验证 Hermes 发布 Server 启动握手")
    parser.add_argument("--server", required=True, type=Path)
    args = parser.parse_args()
    if not args.server.is_file():
        raise SystemExit(f"Server 产物不存在：{args.server}")
    process = subprocess.Popen(
        [str(args.server), "--host", "127.0.0.1", "--port", "0", "--startup-handshake"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    lines: queue.Queue[str] = queue.Queue(maxsize=1)
    threading.Thread(target=_read_line, args=(process.stdout, lines), daemon=True).start()
    try:
        line = lines.get(timeout=15)
        handshake = json.loads(line)
        if (
            handshake.get("type") != "hermes-started"
            or not isinstance(handshake.get("address"), str)
            or not isinstance(handshake.get("release_version"), str)
            or not isinstance(handshake.get("python_package_version"), str)
            or not isinstance(handshake.get("protocol_version"), str)
        ):
            raise RuntimeError("冻结 Server 返回无效启动握手。")
    except Exception:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        assert process.stderr is not None
        diagnostic = process.stderr.read().strip()
        raise RuntimeError(f"冻结 Server 启动失败：{diagnostic}") from None
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
