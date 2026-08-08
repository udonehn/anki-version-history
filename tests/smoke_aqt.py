"""Import smoke test for the desktop APIs required by the add-on."""

from __future__ import annotations

import os


def main() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from aqt import gui_hooks
    from aqt.operations import CollectionOp, QueryOp
    from aqt.taskman import TaskManager

    assert isinstance(QueryOp, type)
    assert isinstance(CollectionOp, type)
    for hook_name in (
        "addon_manager_will_install_addon",
        "addons_dialog_will_delete_addons",
    ):
        hook = getattr(gui_hooks, hook_name)
        assert callable(getattr(hook, "append", None))
    assert callable(getattr(TaskManager, "run_on_main", None))
    assert callable(getattr(TaskManager, "run_in_background", None))


if __name__ == "__main__":
    main()
