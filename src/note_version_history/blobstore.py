"""Content-addressed blob store for media file versions.

Layout: ``<root>/<sha1[:2]>/<sha1>``. Blobs are immutable; identical content
is stored exactly once (this is what keeps media history growth proportional
to what actually changed). Writes are atomic (temp file + os.replace).
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024
_TMP_PREFIX = ".tmp-"
# Spare unreferenced blobs / temp files younger than this: a blob written by an
# in-flight scan is not referenced until that scan commits its event row.
_GC_MIN_AGE_MS = 60 * 60 * 1000


@dataclass(frozen=True)
class BlobStats:
    count: int
    total_bytes: int


class BlobStore:
    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def path_for(self, sha1: str) -> Path:
        return self._root / sha1[:2] / sha1

    def has(self, sha1: str) -> bool:
        return self.path_for(sha1).is_file()

    def put_bytes(self, data: bytes) -> str:
        sha1 = hashlib.sha1(data).hexdigest()
        if not self.has(sha1):
            self._write_atomic(sha1, data)
        return sha1

    def put_file(self, src: Path | str) -> tuple[str, int]:
        """Stream a file into the store; returns (sha1, size).
        No-op (beyond hashing) if the content already exists."""
        digest = hashlib.sha1()
        size = 0
        tmp = self._root / f"{_TMP_PREFIX}{uuid.uuid4().hex}"
        try:
            with open(src, "rb") as fin, open(tmp, "wb") as fout:
                while chunk := fin.read(_CHUNK_SIZE):
                    digest.update(chunk)
                    size += len(chunk)
                    fout.write(chunk)
            sha1 = digest.hexdigest()
            final = self.path_for(sha1)
            if final.is_file():
                tmp.unlink()
            else:
                final.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp, final)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        return sha1, size

    def read_bytes(self, sha1: str) -> bytes:
        return self.path_for(sha1).read_bytes()

    def copy_to(self, sha1: str, dest: Path) -> int:
        """Stream a stored blob to ``dest`` (temp file + os.replace), returning
        bytes written. Avoids loading large media fully into memory. Raises
        FileNotFoundError if the blob is gone."""
        src = self.path_for(sha1)
        tmp = dest.parent / f"{_TMP_PREFIX}{uuid.uuid4().hex}"
        size = 0
        try:
            with open(src, "rb") as fin, open(tmp, "wb") as fout:
                while chunk := fin.read(_CHUNK_SIZE):
                    size += len(chunk)
                    fout.write(chunk)
            os.replace(tmp, dest)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        return size

    def stats(self) -> BlobStats:
        count = 0
        total = 0
        for blob in self._iter_blobs():
            count += 1
            total += blob.stat().st_size
        return BlobStats(count=count, total_bytes=total)

    def gc(
        self,
        referenced: set[str],
        *,
        min_age_ms: int = _GC_MIN_AGE_MS,
        now_ms: int | None = None,
    ) -> int:
        """Delete unreferenced blobs and stale temp files, but SPARE anything
        modified within ``min_age_ms`` — a blob written by an in-flight scan is
        not referenced until that scan commits its event row, so reaping fresh
        orphans would race a concurrent capture. Returns files removed."""
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        cutoff = now - min_age_ms
        removed = 0
        for blob in list(self._iter_blobs()):
            if blob.name in referenced or _mtime_ms(blob) > cutoff:
                continue
            blob.unlink(missing_ok=True)
            removed += 1
        for stale in list(self._iter_tmp_files()):
            if _mtime_ms(stale) > cutoff:
                continue
            stale.unlink(missing_ok=True)
            removed += 1
        return removed

    def _iter_blobs(self):
        for shard in self._root.iterdir():
            if shard.is_dir() and len(shard.name) == 2:
                for blob in shard.iterdir():
                    if blob.is_file() and not blob.name.startswith(_TMP_PREFIX):
                        yield blob

    def _iter_tmp_files(self):
        # put_file writes temps at the root; _write_atomic writes them in the
        # shard dir — sweep both.
        yield from self._root.glob(f"{_TMP_PREFIX}*")
        for shard in self._root.iterdir():
            if shard.is_dir() and len(shard.name) == 2:
                yield from shard.glob(f"{_TMP_PREFIX}*")

    def _write_atomic(self, sha1: str, data: bytes) -> None:
        final = self.path_for(sha1)
        final.parent.mkdir(parents=True, exist_ok=True)
        tmp = final.parent / f"{_TMP_PREFIX}{uuid.uuid4().hex}"
        try:
            tmp.write_bytes(data)
            os.replace(tmp, final)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise


def _mtime_ms(path: Path) -> int:
    try:
        return int(path.stat().st_mtime * 1000)
    except OSError:
        return 0  # vanished mid-sweep: treat as ancient so cleanup proceeds
