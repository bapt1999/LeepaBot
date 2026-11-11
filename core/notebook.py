import fcntl
import hashlib
import json
import os
import stat
import string
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


MAX_FILE_BYTES = 32 * 1024
MAX_STORAGE_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 5000
PAGE_SIZE = 20
READ_CHARS = 4000
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class NotebookError(Exception):
    pass


def _parts(path: str, folder: bool = False) -> tuple[str, ...]:
    if folder and path == "":
        return ()
    if not isinstance(path, str) or not path or len(path) > 200:
        raise NotebookError("Use a relative filename of at most 200 characters.")
    parts = tuple(path.split("/"))
    allowed = set(string.ascii_letters + string.digits + "_- .")
    if len(parts) > 8 or any(
        not p or p.startswith(".") or p != p.strip() or not set(p) <= allowed
        for p in parts
    ):
        raise NotebookError("Use plain relative paths; hidden files and parent paths are unavailable.")
    if not folder and not path.endswith((".md", ".txt")):
        raise NotebookError("Notebook files must end in .md or .txt.")
    return parts


def _revision(text: str | None) -> str:
    return "missing" if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()


@contextmanager
def _directory(base: int, parts: tuple[str, ...], create: bool = False):
    fd = os.dup(base)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _read(parent: int, name: str, limit: int) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise NotebookError("Links and special files are unavailable.")
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise NotebookError("This file exceeds the notebook file limit.")
    return data


def _atomic_write(parent: int, name: str, data: bytes):
    temporary = ".tmp-" + uuid.uuid4().hex
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass


class Notebook:
    def __init__(self, root: str, scope: str):
        self.root = Path(root).expanduser()
        if not self.root.is_absolute() or ".." in self.root.parts:
            raise NotebookError("NOTEBOOK_ROOT must be an absolute path without parent components.")
        self.scope = _parts(scope, folder=True)

    @contextmanager
    def _locked(self):
        # Opening each directory separately prevents following symbolic links.
        anchor = os.open("/", DIR_FLAGS)
        try:
            with _directory(anchor, self.root.parts[1:]) as root:
                info = os.fstat(root)
                if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                    raise NotebookError("The notebook folder must belong to the bot account and have mode 700.")
                lock = os.open(".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=root)
                try:
                    lock_info = os.fstat(lock)
                    if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 1:
                        raise NotebookError("Invalid notebook lock file.")
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        raise NotebookError("The notebook is busy. Try again shortly.") from None
                    self._usage(root)
                    with _directory(root, self.scope, create=True) as scope:
                        with _directory(scope, ("notes",), create=True) as notes:
                            with _directory(scope, ("history",), create=True) as history:
                                self._recover(notes, history)
                                yield root, notes, history
                finally:
                    os.close(lock)
        finally:
            os.close(anchor)

    def _usage(self, root: int) -> tuple[int, int]:
        total = count = 0

        def visit(directory: int, depth: int):
            nonlocal total, count
            if depth > 20:
                raise NotebookError("Notebook directory nesting is too deep.")
            with os.scandir(directory) as entries:
                for entry in entries:
                    count += 1
                    if count > MAX_ENTRIES:
                        raise NotebookError("Notebook entry limit reached; ask the owner to clear space.")
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        with _directory(directory, (entry.name,)) as child:
                            visit(child, depth + 1)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        total += info.st_size
                    else:
                        raise NotebookError("Remove links and special files from the notebook folder.")
        visit(root, 0)
        return total, count

    def _text(self, notes: int, path: str) -> str | None:
        parts = _parts(path)
        try:
            with _directory(notes, parts[:-1]) as parent:
                return _read(parent, parts[-1], MAX_FILE_BYTES).decode("utf-8")
        except FileNotFoundError:
            return None

    def _save(self, history: int, key: str, record: dict):
        _atomic_write(history, key + ".json", json.dumps(record, ensure_ascii=False).encode("utf-8"))

    def _apply(self, notes: int, history: int, key: str, record: dict):
        path = record["path"]
        current = self._text(notes, path)
        before, after = record["before"], record["after"]
        destination = record.get("destination")
        if destination:
            target = self._text(notes, destination)
            if current is None and target == before:
                pass
            elif current == before and target is None:
                source_parts, dest_parts = _parts(path), _parts(destination)
                with _directory(notes, source_parts[:-1]) as source:
                    with _directory(notes, dest_parts[:-1], create=True) as dest:
                        os.rename(source_parts[-1], dest_parts[-1], src_dir_fd=source, dst_dir_fd=dest)
                        os.fsync(source)
                        os.fsync(dest)
            else:
                raise NotebookError("An interrupted move conflicts with a newer edit; ask the owner to inspect history.")
        elif current != after:
            if current != before:
                raise NotebookError("An interrupted write conflicts with a newer edit; ask the owner to inspect history.")
            parts = _parts(path)
            with _directory(notes, parts[:-1], create=True) as parent:
                if after is None:
                    os.unlink(parts[-1], dir_fd=parent)
                    os.fsync(parent)
                else:
                    _atomic_write(parent, parts[-1], after.encode("utf-8"))
        record["status"] = "done"
        self._save(history, key, record)

    def _records(self, history: int):
        with os.scandir(history) as entries:
            names = sorted(entry.name for entry in entries if entry.name.endswith(".json"))
        for name in names:
            yield name[:-5], json.loads(_read(history, name, 512 * 1024))

    def _recover(self, notes: int, history: int):
        for key, record in self._records(history):
            if record["status"] == "pending":
                self._apply(notes, history, key, record)

    def _files(self, notes: int, folder: str):
        found = []

        def visit(directory: int, prefix: str):
            with os.scandir(directory) as entries:
                names = sorted(entry.name for entry in entries if not entry.name.startswith("."))
            for name in names:
                path = f"{prefix}/{name}" if prefix else name
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    with _directory(directory, (name,)) as child:
                        visit(child, path)
                elif name.endswith((".md", ".txt")):
                    _parts(path)
                    found.append({"path": path, "bytes": info.st_size, "modified_at": info.st_mtime})
        with _directory(notes, _parts(folder, folder=True)) as start:
            visit(start, folder)
        return found

    def run(self, name: str, args: dict, operation_id: str, origin: dict, cancelled=lambda: False) -> dict:
        with self._locked() as (root, notes, history):
            if cancelled():
                raise NotebookError("This turn was cancelled.")
            path = args.get("path", "")
            if name in {"list_files", "search_files"}:
                files = self._files(notes, args.get("folder", ""))
                if name == "search_files":
                    query = args["query"].casefold()
                    matches = []
                    for item in files:
                        body = self._text(notes, item["path"]) or ""
                        index = body.casefold().find(query)
                        if index >= 0 or query in item["path"].casefold():
                            start = max(0, index - 100)
                            matches.append({"path": item["path"], "excerpt": body[start:start + 400]})
                    files = matches
                page = args.get("page", 0)
                start = page * PAGE_SIZE
                return {"ok": True, "entries": files[start:start + PAGE_SIZE], "next_page": page + 1 if start + PAGE_SIZE < len(files) else None}
            if name == "read_file":
                body = self._text(notes, path)
                if body is None:
                    raise NotebookError("File not found.")
                offset = args.get("offset", 0)
                end = offset + READ_CHARS
                return {"ok": True, "path": path, "revision": _revision(body), "text": body[offset:end], "next_offset": end if end < len(body) else None}
            if name == "list_versions":
                _parts(path)
                versions = []
                for key, record in self._records(history):
                    if record["path"] == path or record.get("destination") == path:
                        versions.append({"version": key, "action": record["action"], "time": record["time"], "deleted": record["after"] is None})
                versions.sort(key=lambda item: item["time"], reverse=True)
                page = args.get("page", 0)
                start = page * PAGE_SIZE
                return {"ok": True, "versions": versions[start:start + PAGE_SIZE], "next_page": page + 1 if start + PAGE_SIZE < len(versions) else None}

            key = hashlib.sha256(operation_id.encode()).hexdigest()
            try:
                record = json.loads(_read(history, key + ".json", 512 * 1024))
                return record["result"]
            except FileNotFoundError:
                pass
            before = self._text(notes, path)
            if args["revision"] != _revision(before):
                raise NotebookError("Revision conflict. Read the file again before changing it; use 'missing' to create a new file.")
            destination = None
            if name == "write_file":
                after = args["text"]
            elif name == "append_file":
                if before is None:
                    raise NotebookError("File not found; create it with write_file first.")
                after = before + args["text"]
            elif name in {"move_file", "delete_file"}:
                if before is None:
                    raise NotebookError("File not found.")
                after = None if name == "delete_file" else before
                if name == "move_file":
                    destination = args["destination"]
                    if self._text(notes, destination) is not None:
                        raise NotebookError("The destination already exists.")
            elif name == "restore_file":
                version = args["version"]
                if len(version) != 64 or any(c not in string.hexdigits for c in version):
                    raise NotebookError("Choose a version from list_versions.")
                old = json.loads(_read(history, version + ".json", 512 * 1024))
                if path not in (old["path"], old.get("destination")):
                    raise NotebookError("That version belongs to a different file.")
                after = old["before"] if old["action"] == "delete_file" else old["after"]
                if after is None:
                    raise NotebookError("That version contains no saved text.")
            else:
                raise NotebookError("Unknown notebook operation.")
            if after is not None and len(after.encode("utf-8")) > MAX_FILE_BYTES:
                raise NotebookError("File limit is 32 KiB; split this into smaller notes.")
            result = {"ok": True, "path": destination or path, "revision": _revision(after), "version": key}
            record = {"status": "pending", "action": name, "path": path, "destination": destination, "before": before, "after": after, "origin": origin, "time": time.time(), "result": result}
            size, count = self._usage(root)
            extra = len(json.dumps(record, ensure_ascii=False).encode()) + len((after or "").encode()) + 4096
            if size + extra > MAX_STORAGE_BYTES or count + 20 > MAX_ENTRIES:
                raise NotebookError("Notebook storage limit reached. Ask the owner to back up and clear history.")
            if cancelled():
                raise NotebookError("This turn was cancelled.")
            self._save(history, key, record)
            self._apply(notes, history, key, record)
            return result