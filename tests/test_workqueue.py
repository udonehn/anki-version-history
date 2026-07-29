from note_version_history.workqueue import (
    PendingWork,
    callback_is_current,
    heal_scope,
    retry_delay,
    shutdown_can_be_clean,
)


def test_failed_work_merges_before_new_pending_without_losing_flags():
    failed = PendingWork(
        labels=["undo", "@sync"],
        saw_undo=True,
        want_notes=True,
        force_deletion_diff=True,
        recheck_nids=frozenset({1, 2}),
    )
    pending = PendingWork(
        labels=["new edit"],
        want_notetypes=True,
        recheck_nids=frozenset({3}),
    )
    pending.merge_before(failed)
    assert pending.labels == ["undo", "@sync", "new edit"]
    assert pending.saw_undo and pending.want_notes and pending.want_notetypes
    assert pending.force_deletion_diff
    assert pending.recheck_nids == frozenset({1, 2, 3})


def test_retry_backoff_and_runtime_identity_guard():
    assert [retry_delay(value) for value in range(1, 7)] == [
        1_000,
        5_000,
        30_000,
        120_000,
        300_000,
        300_000,
    ]
    first = object()
    second = object()
    assert callback_is_current(first, first)
    assert not callback_is_current(second, first)


def test_consume_returns_snapshot_and_clears_every_flag():
    pending = PendingWork(
        labels=["edit"],
        saw_undo=True,
        want_notes=True,
        want_notetypes=True,
        force_deletion_diff=True,
        recheck_nids=frozenset({7}),
    )
    taken = pending.consume()
    assert taken.labels == ["edit"]
    assert taken.saw_undo and taken.want_notes and taken.want_notetypes
    assert taken.force_deletion_diff and taken.recheck_nids == frozenset({7})
    assert pending == PendingWork()


def test_unclean_heal_scope_and_clean_shutdown_policy():
    assert heal_scope(False, False) is None
    assert heal_scope(True, True) == "full"
    assert heal_scope(True, False) == "indexed"
    assert shutdown_can_be_clean(
        mutation_busy=False,
        sync_active=False,
        full_rescan_pending=False,
        final_scan_succeeded=True,
    )
    for blocked in ("mutation_busy", "sync_active", "full_rescan_pending"):
        values = {
            "mutation_busy": False,
            "sync_active": False,
            "full_rescan_pending": False,
            "final_scan_succeeded": True,
        }
        values[blocked] = True
        assert not shutdown_can_be_clean(**values)
    assert not shutdown_can_be_clean(
        mutation_busy=False,
        sync_active=False,
        full_rescan_pending=False,
        final_scan_succeeded=False,
    )
