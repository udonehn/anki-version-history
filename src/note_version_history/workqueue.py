"""Headless pending-work merge and retry policy used by the Qt scheduler."""

from __future__ import annotations

from dataclasses import dataclass, field

SCAN_RETRY_DELAYS_MS = (1_000, 5_000, 30_000, 120_000, 300_000)


@dataclass
class PendingWork:
    labels: list[str] = field(default_factory=list)
    saw_undo: bool = False
    want_notes: bool = False
    want_notetypes: bool = False
    force_deletion_diff: bool = False
    recheck_nids: frozenset[int] = frozenset()

    def consume(self) -> "PendingWork":
        taken = PendingWork(
            labels=list(self.labels),
            saw_undo=self.saw_undo,
            want_notes=self.want_notes,
            want_notetypes=self.want_notetypes,
            force_deletion_diff=self.force_deletion_diff,
            recheck_nids=self.recheck_nids,
        )
        self.labels.clear()
        self.saw_undo = False
        self.want_notes = False
        self.want_notetypes = False
        self.force_deletion_diff = False
        self.recheck_nids = frozenset()
        return taken

    def merge_before(self, older: "PendingWork") -> None:
        """Requeue failed work ahead of changes received while it ran."""
        self.labels = list(older.labels) + self.labels
        self.saw_undo |= older.saw_undo
        self.want_notes |= older.want_notes
        self.want_notetypes |= older.want_notetypes
        self.force_deletion_diff |= older.force_deletion_diff
        self.recheck_nids |= older.recheck_nids


def retry_delay(failures: int) -> int:
    index = min(max(1, int(failures)) - 1, len(SCAN_RETRY_DELAYS_MS) - 1)
    return SCAN_RETRY_DELAYS_MS[index]


def callback_is_current(current: object | None, token: object) -> bool:
    return current is token


def heal_scope(unclean_shutdown: bool, baseline_done: bool) -> str | None:
    if not unclean_shutdown:
        return None
    return "full" if baseline_done else "indexed"


def shutdown_can_be_clean(
    *,
    mutation_busy: bool,
    sync_active: bool,
    full_rescan_pending: bool,
    final_scan_succeeded: bool,
) -> bool:
    return (
        not mutation_busy
        and not sync_active
        and not full_rescan_pending
        and final_scan_succeeded
    )
