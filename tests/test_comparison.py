from note_version_history import comparison
from note_version_history.records import NotetypeVersion, NoteVersion


def _note(*, names=("Front",), fields=("value",), tags=("tag",), deleted=False):
    return NoteVersion(
        nid=1,
        guid="g",
        mid=1,
        ts=1,
        origin="auto",
        op_label="",
        fields=fields,
        field_names=names,
        tags=tags,
        hash="h",
        deleted=deleted,
    )


def _notetype(config: str, *, deleted: bool = False):
    return NotetypeVersion(
        mid=1,
        ts=1,
        origin="auto",
        op_label="",
        name="Basic",
        config_json=config,
        hash="h",
        deleted=deleted,
    )


def test_note_compare_uses_field_union_tags_and_empty_deleted_state():
    a = _note(names=("Front", "Old"), fields=("a", "old"), tags=("x",))
    b = _note(names=("Front", "New"), fields=("b", "new"), tags=("y",))
    result = comparison.compare_notes(a, b)
    assert result.field_names == ("Front", "Old", "New")
    assert result.a_tags == ("x",) and result.b_tags == ("y",)

    deleted = comparison.compare_notes(a, _note(deleted=True))
    assert deleted.b_fields == {}
    assert deleted.b_tags == ()


def test_notetype_compare_uses_template_union_css_and_empty_deleted_state():
    a = _notetype('{"tmpls":[{"name":"A","qfmt":"a"}],"css":"old"}')
    b = _notetype('{"tmpls":[{"name":"B","qfmt":"b"}],"css":"new"}')
    result = comparison.compare_notetypes(a, b)
    assert result.template_names == ("A", "B")
    assert result.a_css == "old" and result.b_css == "new"

    deleted = comparison.compare_notetypes(a, _notetype("", deleted=True))
    assert deleted.b_templates == {}
    assert deleted.b_css == ""


def test_note_action_target_is_the_rendered_b_endpoint_in_explicit_mode():
    current = _note(fields=("selected",))
    endpoint_a = _note(fields=("before",))
    endpoint_b = _note(fields=("rendered B",))

    assert (
        comparison.action_target(
            current,
            explicit_mode=False,
            endpoint_a=endpoint_a,
            endpoint_b=endpoint_b,
        )
        is current
    )
    assert (
        comparison.action_target(
            current,
            explicit_mode=True,
            endpoint_a=endpoint_a,
            endpoint_b=endpoint_b,
        )
        is endpoint_b
    )
    assert (
        comparison.action_target(
            current,
            explicit_mode=True,
            endpoint_a=None,
            endpoint_b=endpoint_b,
        )
        is None
    )


def test_notetype_action_target_is_the_rendered_b_endpoint_in_explicit_mode():
    current = _notetype('{"css":"selected"}')
    endpoint_a = _notetype('{"css":"before"}')
    endpoint_b = _notetype('{"css":"rendered B"}')

    assert (
        comparison.action_target(
            current,
            explicit_mode=True,
            endpoint_a=endpoint_a,
            endpoint_b=endpoint_b,
        )
        is endpoint_b
    )
    assert (
        comparison.action_target(
            current,
            explicit_mode=True,
            endpoint_a=endpoint_a,
            endpoint_b=None,
        )
        is None
    )
