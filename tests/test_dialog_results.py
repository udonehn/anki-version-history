from note_version_history.dialog_results import accepted_choice


def test_choice_dialog_cancel_close_empty_and_ok_results():
    choices = [10, 20]
    assert accepted_choice(False, choices, 1) is None
    assert accepted_choice(False, choices, -1) is None
    assert accepted_choice(True, [], 0) is None
    assert accepted_choice(True, choices, -1) is None
    assert accepted_choice(True, choices, 2) is None
    assert accepted_choice(True, choices, 1) == 20
