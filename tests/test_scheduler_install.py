from __future__ import annotations

import importlib
import sqlite3
import sys
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from types import ModuleType, SimpleNamespace

import pytest

from note_version_history import consts, db, profiles
from note_version_history.appconfig import AddonConfig
from note_version_history.workqueue import PendingWork


class FakeTimer:
    def __init__(self, *_args) -> None:
        self.stopped = False
        self.stop_calls = 0
        self.started_with: list[int] = []

    def stop(self) -> None:
        self.stopped = True
        self.stop_calls += 1

    def start(self, interval: int) -> None:
        self.started_with.append(interval)


class FakeConnection:
    def __init__(self, error: sqlite3.Error | None = None) -> None:
        self.closed = False
        self.error = error

    def close(self) -> None:
        self.closed = True
        if self.error is not None:
            raise self.error


class FakeTaskManager:
    def __init__(self) -> None:
        self.main_handoffs = 0
        self.collection_barriers = 0

    def run_on_main(self, callback) -> None:
        self.main_handoffs += 1
        callback()

    def run_in_background(self, callback) -> Future:
        self.collection_barriers += 1
        future: Future = Future()
        try:
            future.set_result(callback())
        except BaseException as exc:
            future.set_exception(exc)
        return future


@pytest.fixture
def scheduler_module(monkeypatch):
    # Import the package once while aqt is absent, so its guarded entry point
    # does not eagerly wire the scheduler before the lightweight stubs exist.
    import note_version_history  # noqa: F401

    taskman = FakeTaskManager()
    fake_mw = SimpleNamespace(
        taskman=taskman,
        addonManager=SimpleNamespace(addonFromModule=lambda _name: "1237174160"),
    )
    fake_hooks = SimpleNamespace()
    fake_aqt = ModuleType("aqt")
    fake_aqt.gui_hooks = fake_hooks
    fake_aqt.mw = fake_mw
    fake_operations = ModuleType("aqt.operations")
    fake_operations.QueryOp = object
    fake_qt = ModuleType("aqt.qt")
    fake_qt.QTimer = object
    fake_qt.qconnect = lambda *_args, **_kwargs: None

    monkeypatch.setitem(sys.modules, "aqt", fake_aqt)
    monkeypatch.setitem(sys.modules, "aqt.operations", fake_operations)
    monkeypatch.setitem(sys.modules, "aqt.qt", fake_qt)
    sys.modules.pop("note_version_history.scheduler", None)
    scheduler = importlib.import_module("note_version_history.scheduler")
    yield scheduler, taskman
    sys.modules.pop("note_version_history.scheduler", None)


def make_runtime(connection: FakeConnection):
    return SimpleNamespace(
        conn=connection,
        debounce=FakeTimer(),
        heartbeat=FakeTimer(),
        mutation_owner=None,
        sync_active=False,
        full_rescan_pending=False,
    )


def test_worker_update_closes_on_main_before_return(scheduler_module, monkeypatch):
    scheduler, taskman = scheduler_module
    connection = FakeConnection()
    runtime = make_runtime(connection)
    scheduler._runtime = runtime
    worker = object()
    owner = object()
    monkeypatch.setattr(scheduler, "current_thread", lambda: worker)
    monkeypatch.setattr(scheduler, "main_thread", lambda: owner)
    manager = SimpleNamespace(addonFromModule=lambda _name: "1237174160")

    scheduler._on_addon_manager_will_install(manager, "1237174160")

    assert connection.closed
    assert runtime.debounce.stopped and runtime.heartbeat.stopped
    assert scheduler.runtime() is None
    assert taskman.main_handoffs == 1
    assert taskman.collection_barriers == 0


def test_local_install_drains_collection_executor(scheduler_module, monkeypatch):
    scheduler, taskman = scheduler_module
    connection = FakeConnection()
    scheduler._runtime = make_runtime(connection)
    owner = object()
    monkeypatch.setattr(scheduler, "current_thread", lambda: owner)
    monkeypatch.setattr(scheduler, "main_thread", lambda: owner)
    manager = SimpleNamespace(addonFromModule=lambda _name: "note_version_history")

    scheduler._on_addon_manager_will_install(manager, "note_version_history")

    assert connection.closed
    assert taskman.main_handoffs == 0
    assert taskman.collection_barriers == 1


def test_local_install_timeout_aborts_install(scheduler_module, monkeypatch):
    scheduler, taskman = scheduler_module
    connection = FakeConnection()
    scheduler._runtime = make_runtime(connection)
    owner = object()
    monkeypatch.setattr(scheduler, "current_thread", lambda: owner)
    monkeypatch.setattr(scheduler, "main_thread", lambda: owner)
    manager = SimpleNamespace(addonFromModule=lambda _name: "note_version_history")

    class TimedOutBarrier:
        def result(self, *, timeout):
            assert timeout == scheduler._INSTALL_TEARDOWN_TIMEOUT_SECONDS
            raise FutureTimeoutError

    monkeypatch.setattr(taskman, "run_in_background", lambda _callback: TimedOutBarrier())

    with pytest.raises(RuntimeError, match="timed out waiting"):
        scheduler._on_addon_manager_will_install(manager, "note_version_history")

    assert connection.closed


def test_other_addon_install_is_ignored(scheduler_module):
    scheduler, taskman = scheduler_module
    connection = FakeConnection()
    scheduler._runtime = make_runtime(connection)
    manager = SimpleNamespace(addonFromModule=lambda _name: "1237174160")

    scheduler._on_addon_manager_will_install(manager, "1771074083")

    assert not connection.closed
    assert taskman.main_handoffs == 0
    assert taskman.collection_barriers == 0


def test_close_failure_aborts_install(scheduler_module, monkeypatch):
    scheduler, _taskman = scheduler_module
    scheduler._runtime = make_runtime(FakeConnection(sqlite3.OperationalError("busy")))
    worker = object()
    owner = object()
    monkeypatch.setattr(scheduler, "current_thread", lambda: worker)
    monkeypatch.setattr(scheduler, "main_thread", lambda: owner)
    manager = SimpleNamespace(addonFromModule=lambda _name: "1237174160")

    with pytest.raises(sqlite3.OperationalError, match="busy"):
        scheduler._on_addon_manager_will_install(manager, "1237174160")

    assert scheduler.runtime() is None


def test_disabling_auto_capture_stops_debounce_and_heartbeat(
    scheduler_module, monkeypatch
):
    scheduler, _taskman = scheduler_module
    runtime = make_runtime(FakeConnection())
    scheduler._runtime = runtime
    monkeypatch.setattr(scheduler, "apply_language", lambda: None)
    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: AddonConfig(auto_capture=False, heartbeat_scan_minutes=5),
    )

    scheduler._on_config_updated({})

    assert runtime.debounce.stop_calls == 1
    assert runtime.heartbeat.stop_calls == 1
    assert runtime.heartbeat.started_with == []


def test_enabled_config_update_preserves_debounce_and_restarts_heartbeat(
    scheduler_module, monkeypatch
):
    scheduler, _taskman = scheduler_module
    runtime = make_runtime(FakeConnection())
    scheduler._runtime = runtime
    monkeypatch.setattr(scheduler, "apply_language", lambda: None)
    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: AddonConfig(auto_capture=True, heartbeat_scan_minutes=5),
    )

    scheduler._on_config_updated({})

    assert runtime.debounce.stop_calls == 0
    assert runtime.heartbeat.stop_calls == 1
    assert runtime.heartbeat.started_with == [300_000]


def test_disabled_auto_capture_leaves_normal_pending_work_unconsumed(
    scheduler_module, monkeypatch
):
    scheduler, _taskman = scheduler_module
    pending = PendingWork(want_notes=True, want_notetypes=True)
    runtime = SimpleNamespace(
        sync_active=False,
        baseline_running=False,
        mutation_owner=None,
        full_rescan_pending=False,
        pending=pending,
    )
    scheduler._runtime = runtime
    scheduler.mw.col = object()
    monkeypatch.setattr(
        scheduler, "load_config", lambda: AddonConfig(auto_capture=False)
    )

    scheduler._start_scan()

    assert pending.want_notes
    assert pending.want_notetypes


def test_disabled_auto_capture_still_drains_manual_full_rescan(
    scheduler_module, monkeypatch
):
    scheduler, _taskman = scheduler_module
    done = object()
    runtime = SimpleNamespace(
        sync_active=False,
        baseline_running=False,
        mutation_owner=None,
        full_rescan_pending=True,
        pending_rescan_done=done,
    )
    scheduler._runtime = runtime
    scheduler.mw.col = object()
    calls = []
    monkeypatch.setattr(scheduler, "request_full_rescan", calls.append)
    monkeypatch.setattr(
        scheduler, "load_config", lambda: AddonConfig(auto_capture=False)
    )

    scheduler._start_scan()

    assert calls == [done]
    assert not runtime.full_rescan_pending


class ProfileTimer(FakeTimer):
    instances: list["ProfileTimer"] = []
    single_shots: list[tuple[int, object]] = []

    def __init__(self, *_args) -> None:
        super().__init__()
        self.timeout = object()
        self.single_shot = False
        self.instances.append(self)

    def setSingleShot(self, value: bool) -> None:
        self.single_shot = value

    @classmethod
    def singleShot(cls, delay: int, callback) -> None:
        cls.single_shots.append((delay, callback))


def _prepare_profile_open(scheduler, monkeypatch, user_files, config):
    ProfileTimer.instances = []
    ProfileTimer.single_shots = []
    scheduler.mw.pm = SimpleNamespace(name="Profile", profile={}, save=lambda: None)
    scheduler.mw.col = object()
    scheduler.mw.progress = SimpleNamespace(busy=lambda: False)
    monkeypatch.setattr(scheduler, "QTimer", ProfileTimer)
    monkeypatch.setattr(scheduler, "qconnect", lambda *_args: None)
    monkeypatch.setattr(scheduler, "user_files_dir", lambda: user_files)
    monkeypatch.setattr(scheduler, "load_config", lambda: config)
    monkeypatch.setattr(scheduler, "apply_language", lambda: None)
    monkeypatch.setattr(scheduler, "_safe_undo_status", lambda: None)


def test_fresh_disabled_profile_sets_boundary_without_automatic_work(
    scheduler_module, monkeypatch, tmp_path
):
    scheduler, _taskman = scheduler_module
    config = AddonConfig(
        auto_capture=False,
        heartbeat_scan_minutes=5,
        capture_media=True,
        media_scan_on_profile_open=True,
    )
    _prepare_profile_open(scheduler, monkeypatch, tmp_path, config)
    boundary_calls = []
    notetype_calls = []
    monkeypatch.setattr(
        scheduler.capture_notes,
        "initialize_lazy_boundary",
        lambda col, conn: boundary_calls.append((col, conn)),
    )
    monkeypatch.setattr(
        scheduler.capture_notetypes,
        "scan_notetypes",
        lambda *_args, **_kwargs: notetype_calls.append(True),
    )
    monkeypatch.setattr(
        scheduler, "request_scan", lambda **_kwargs: pytest.fail("catch-up scheduled")
    )
    monkeypatch.setattr(
        scheduler,
        "request_full_rescan",
        lambda *_args: pytest.fail("heal scheduled"),
    )
    monkeypatch.setattr(
        scheduler, "request_media_scan", lambda: pytest.fail("media scan scheduled")
    )

    scheduler._on_profile_open()

    assert len(boundary_calls) == 1
    assert not notetype_calls
    assert len(ProfileTimer.instances) == 2
    assert ProfileTimer.instances[1].started_with == []
    scheduler._close_runtime()


@pytest.mark.parametrize("unclean", [False, True])
def test_existing_disabled_profile_skips_catchup_heal_heartbeat_and_media(
    scheduler_module, monkeypatch, tmp_path, unclean
):
    scheduler, _taskman = scheduler_module
    config = AddonConfig(
        auto_capture=False,
        heartbeat_scan_minutes=5,
        capture_media=True,
        media_scan_on_profile_open=True,
    )
    _prepare_profile_open(scheduler, monkeypatch, tmp_path, config)
    storage_key, _changed = profiles.choose_storage_key(tmp_path, "Profile", None)
    scheduler.mw.pm.profile[profiles.PROFILE_STORAGE_KEY] = storage_key
    data_dir = profiles.profile_data_dir_for_key(tmp_path, storage_key)
    connection = db.open_history_db(profiles.history_db_path(data_dir))
    db.meta_set(connection, consts.META_NOTE_SCAN_MARKER, "1")
    db.meta_set(connection, consts.META_CLEAN_SHUTDOWN, "0" if unclean else "1")
    connection.close()
    monkeypatch.setattr(
        scheduler, "request_scan", lambda **_kwargs: pytest.fail("catch-up scheduled")
    )
    monkeypatch.setattr(
        scheduler,
        "request_full_rescan",
        lambda *_args: pytest.fail("heal scheduled"),
    )
    monkeypatch.setattr(
        scheduler, "request_media_scan", lambda: pytest.fail("media scan scheduled")
    )

    scheduler._on_profile_open()

    assert len(ProfileTimer.instances) == 2
    assert ProfileTimer.instances[1].started_with == []
    assert [delay for delay, _callback in ProfileTimer.single_shots] == [
        scheduler._PROFILE_OPEN_PROMPT_DELAY_MS
    ]
    scheduler._close_runtime()
