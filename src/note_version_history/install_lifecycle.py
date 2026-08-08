"""Thread hand-off helpers used while Anki replaces the add-on directory."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event


class OwnerThreadTimeout(RuntimeError):
    """Raised when an owner-thread callback cannot complete before install."""


def run_on_owner_thread_sync(
    callback: Callable[[], None],
    schedule_on_owner: Callable[[Callable[[], None]], None],
    *,
    already_on_owner: bool,
    timeout_seconds: float,
) -> None:
    """Run ``callback`` on its owner thread and do not return before it ends.

    AnkiWeb installs add-ons on a worker thread, while a local ``.ankiaddon``
    install runs on the Qt main thread. Queueing and waiting from the owner
    thread would deadlock, so that path executes the callback inline.
    """
    if already_on_owner:
        callback()
        return

    done = Event()
    errors: list[BaseException] = []

    def wrapped() -> None:
        try:
            callback()
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()

    schedule_on_owner(wrapped)
    if not done.wait(timeout_seconds):
        raise OwnerThreadTimeout(
            "timed out while closing add-on files; refusing to continue installation"
        )
    if errors:
        raise errors[0]
