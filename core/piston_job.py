"""Sent to Piston as the job's entry point; never executed by LeepaBot."""

import base64
import json
import os
from pathlib import Path, PurePosixPath
import selectors
import signal
import stat
import subprocess
import sys
import time


TEXT_BYTES = 4096
PNG_BYTES = 262144
RUN_SECONDS = 8.0


def read_image(directory, name):
    path = PurePosixPath(name)
    if (
        not name or len(name) > 200 or path.is_absolute()
        or ".." in path.parts or "\\" in name
        or any(ord(ch) < 32 for ch in name) or path.suffix.lower() != ".png"
    ):
        raise ValueError("Use a relative .png path without parent directories.")
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= PNG_BYTES:
                raise ValueError("The image must be a regular file of at most 256 KiB.")
            data = stream.read(PNG_BYTES + 1)
        if len(data) > PNG_BYTES or not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("The requested file is not a supported PNG.")
        return base64.b64encode(data).decode("ascii")
    finally:
        os.close(directory_fd)


def stop_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def main():
    source = Path(__file__).resolve().parent
    request = json.loads((source / "request.json").read_text())
    work = source / "work"
    work.mkdir()
    environment = dict(os.environ)
    environment.update({
        "MPLBACKEND": "Agg", "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
        "NUMEXPR_MAX_THREADS": "1", "PYTHONIOENCODING": "utf-8",
    })
    process = subprocess.Popen(
        [sys.executable, "-I", "-u", str(source / "user_code.py")],
        cwd=work, env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
    )
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = False
    timed_out = False
    deadline = time.monotonic() + RUN_SECONDS
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                for key, _ in selector.select(min(remaining, 0.1)):
                    data = os.read(key.fileobj.fileno(), 8192)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    buffer = buffers[key.data]
                    space = TEXT_BYTES - len(buffer)
                    buffer.extend(data[:space])
                    truncated |= len(data) > space
        if not timed_out:
            try:
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
    finally:
        stop_group(process)
        process.wait(timeout=0.5)
        process.stdout.close()
        process.stderr.close()

    result = {
        "protocol": "leepa-python-1",
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "stdout": buffers["stdout"].decode("utf-8", errors="replace"),
        "stderr": buffers["stderr"].decode("utf-8", errors="replace"),
        "text_truncated": truncated,
    }
    if request.get("image_path") and process.returncode == 0 and not timed_out:
        try:
            result["png"] = read_image(work, request["image_path"])
        except (OSError, ValueError) as exc:
            result["image_error"] = str(exc)[:200]
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()