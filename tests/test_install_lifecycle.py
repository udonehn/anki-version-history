from __future__ import annotations

import os
import sqlite3
from queue import Queue
from threading import Thread

import pytest

from note_version_history.install_lifecycle import (
    OwnerThreadTimeout,
    run_on_owner_thread_sync,
)


def test_owner_thread_runs_inline_without_scheduling():
    calls: list[str] = []

    def must_not_schedule(_callback):
        raise AssertionError("owner-thread callback must run inline")

    run_on_owner_thread_sync(
        lambda: calls.append("closed"),
        must_not_schedule,
        already_on_owner=True,
        timeout_seconds=0.1,
    )

    assert calls == ["closed"]


def test_worker_waits_for_scheduled_owner_callback():
    calls: list[str] = []

    run_on_owner_thread_sync(
        lambda: calls.append("closed"),
        lambda callback: callback(),
        already_on_owner=False,
        timeout_seconds=0.1,
    )

    assert calls == ["closed"]


def test_owner_callback_error_is_propagated_to_installer():
    expected = RuntimeError("close failed")

    def fail() -> None:
        raise expected

    with pytest.raises(RuntimeError) as exc_info:
        run_on_owner_thread_sync(
            fail,
            lambda callback: callback(),
            already_on_owner=False,
            timeout_seconds=0.1,
        )

    assert exc_info.value is expected


def test_timeout_refuses_to_continue_installation():
    with pytest.raises(OwnerThreadTimeout):
        run_on_owner_thread_sync(
            lambda: None,
            lambda _callback: None,
            already_on_owner=False,
            timeout_seconds=0.001,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows file-lock semantics")
def test_owner_handoff_releases_sqlite_directory_lock_on_windows(tmp_path):
    user_files = tmp_path / "addon" / "user_files"
    user_files.mkdir(parents=True)
    connection = sqlite3.connect(user_files / "history.db")
    connection.execute("create table sample(value integer)")
    connection.commit()

    owner_queue: Queue = Queue()
    worker_errors: list[BaseException] = []

    def worker() -> None:
        try:
            run_on_owner_thread_sync(
                connection.close,
                owner_queue.put,
                already_on_owner=False,
                timeout_seconds=2.0,
            )
        except BaseException as exc:
            worker_errors.append(exc)

    thread = Thread(target=worker)
    thread.start()
    close_on_owner = owner_queue.get(timeout=1.0)

    with pytest.raises(PermissionError):
        user_files.rename(tmp_path / "files_backup")

    close_on_owner()
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    assert not worker_errors

    user_files.rename(tmp_path / "files_backup")
    assert (tmp_path / "files_backup" / "history.db").exists()
