"""User-visible strings. English and Korean are kept at full key parity
(enforced by tests/test_i18n.py::test_en_ko_key_parity). Keys grouped by
surface."""

from __future__ import annotations

_EN = {
    # General
    "addon_name": "Version History",
    # Tools menu
    "menu_root": "Note Version History",
    "menu_about": "About / Statistics…",
    # About dialog
    "about_title": "Version History — Statistics",
    "about_body": (
        "Note versions: {notes}\n"
        "Note type versions: {notetypes}\n"
        "Media events: {media}\n"
        "Blob store: {blob_count} files, {blob_mb:.1f} MB\n\n"
        "Database: {db_path}"
    ),
    "about_no_profile": "No profile is open.",
    # Errors
    "db_too_new": (
        "The version history database was created by a newer version of this "
        "add-on. Please update the add-on."
    ),
    "db_open_failed": "Could not open the version history database:\n{error}",
    # Capture pipeline
    "label_undo": "Undo: {label}",
    "label_redo": "Redo: {label}",
    "scan_failed": "Version capture failed:\n{error}",
    # Baseline wizard
    "baseline_intro_title": "Version History — Full Baseline",
    "baseline_intro": (
        "Notes are already captured automatically as you edit them. This adds "
        "a one-time baseline of your ENTIRE collection, so even notes you "
        "haven't edited yet stay restorable (e.g. after Find & Replace or "
        "sync).\n\n"
        "Notes: {notes} (~{mb:.1f} MB of text)\n"
        "Note types: {notetypes}\n\n"
        "Runs in the background and only reads your collection. Start now?"
    ),
    "baseline_resume_prompt": (
        "A previous baseline run was interrupted. Resume it now?\n\n"
        "Notes: {notes} (~{mb:.1f} MB of text)\n"
        "Note types: {notetypes}"
    ),
    "baseline_progress": "Capturing baseline…",
    "baseline_progress_label": "Baseline: {done} / {total} notes",
    "baseline_done": "Version History baseline complete ({count} notes).",
    "baseline_postponed": (
        "Cancelled — you can run the full baseline anytime from the Tools menu."
    ),
    "baseline_failed": "Baseline failed:\n{error}",
    # Browser / editor entry points
    "menu_note_history": "🕘 Version History…",
    "no_note_selected": "Select a note first.",
    "menu_snapshot_selected": "Snapshot {count} Selected Note(s)",
    "snapshot_done": "Snapshot saved ({count} note(s)).",
    "editor_history_tip": "Version history (snapshot and restore)",
    "editor_unsaved_note": "Save the note first (it has no id yet).",
    "no_profile_open": "No profile is open.",
    # History dialog
    "hd_title": "Version History — Note {nid}",
    "hd_no_versions": "No versions recorded for this note yet.",
    "hd_view_only": "Show this version only (no comparison)",
    "hd_diff_vs_current": "Compare with current note",
    "hd_diff_vs_previous": "Compare with previous version",
    "hd_restore_version": "Restore This Version",
    "hd_restore_as_new": "Restore as New Note…",
    "hd_restore_fields": "Restore Selected Fields",
    "hd_snapshot_now": "Add Snapshot",
    "hd_close": "Close",
    "hd_tags": "Tags",
    "hd_deleted_banner": "This version marks the note's DELETION.",
    "hd_note_missing_banner": "The note no longer exists in the collection.",
    "origin_baseline": "Baseline",
    "origin_auto": "Auto",
    "origin_manual": "Snapshot",
    "origin_restore": "Restore",
    # System-event timeline labels (stored as "@" sentinels, translated here)
    "@delete_note": "Deleted note",
    "@undo_delete": "Restored (undo delete)",
    "@full_rescan": "Full rescan",
    "@delete_notetype": "Deleted note type",
    # Restore flows
    "undo_restore_note": "Restore Note Version",
    "undo_restore_as_new": "Restore Note as New",
    "confirm_restore": "Overwrite the current note content with the version from {when}?",
    "no_fields_selected": "Select at least one field to restore.",
    "restore_done": "Note version restored (Ctrl+Z to undo).",
    "restore_fields_done": "Fields restored: {fields}",
    "restore_skipped_fields": (
        "Some stored fields have no match in the current note type and were "
        "skipped: {fields}"
    ),
    "restore_failed": "Restore failed:\n{error}",
    "restore_guid_mismatch": (
        "This note id now belongs to a DIFFERENT note (the original was "
        "deleted and the id reused, e.g. by an import). In-place restore is "
        "blocked to protect the current note — use \"Restore as New Note\" "
        "instead."
    ),
    "restore_as_new_prompt": (
        "The original note is gone. Restore this version's content as a NEW "
        "note?"
    ),
    "restore_pick_deck": "Restore into which deck?",
    "restore_as_new_done": "Restored as a new note (Ctrl+Z to undo).",
    # Note type history dialog
    "menu_notetype_history": "Note Type History…",
    "clayout_history_button": "🕘 Version history…",
    "ntd_title": "Version History — Note Types",
    "ntd_pick": "Note type:",
    "ntd_diff_vs_current": "Compare with current note type (incl. unsaved edits)",
    "ntd_no_versions": "No versions recorded for this note type yet.",
    "ntd_front": "Front template",
    "ntd_back": "Back template",
    "ntd_css_tab": "CSS",
    "ntd_deleted_banner": "This version marks the note type's DELETION.",
    "ntd_notetype_missing": "This note type no longer exists in the collection.",
    "ntd_restore": "Restore Templates + CSS",
    "ntd_confirm_restore": (
        "Overwrite the current templates and CSS of \"{name}\" with the "
        "version from {when}?\n\n(Fields and scheduling are not touched — "
        "this does NOT force a full sync.)"
    ),
    "undo_restore_notetype": "Restore Note Type Version",
    "ntd_restore_done": "Templates + CSS restored (Ctrl+Z to undo).",
    "ntd_mismatch_warning": (
        "Template name mismatches during restore.\n"
        "Applied: {applied}\n"
        "In version but not in current note type (skipped): {missing_current}\n"
        "In current note type but not in version (kept): {missing_stored}"
    ),
    "ntd_snapshot_done": "Note type snapshot saved.",
    "ntd_deleted_suffix": "(deleted)",
    "ntd_loaded_into_editor": (
        "Loaded this version into the open template editor — review the "
        "preview and press Save to apply (or close without saving to discard)."
    ),
    "ntd_editor_conflict": (
        "The template editor for this note type is open and could not be "
        "refreshed. Close the editor and try the restore again."
    ),
    # Media
    "menu_media_history": "Media History…",
    "menu_resume_media_baseline": "Baseline Media Files…",
    "menu_baseline_now": "Baseline Entire Collection…",
    "md_title": "Version History — Media",
    "md_filter": "Filter:",
    "md_scan_now": "Scan Media Now",
    "md_restore": "Restore This Version",
    "md_no_selection": "Select a file and a version.",
    "md_restore_confirm": "Overwrite \"{fname}\" on disk with the selected version?",
    "md_restore_done": "Media file restored.",
    "md_restore_failed": "Media restore failed:\n{error}",
    "md_scan_done": "Media scan: +{added} / ~{modified} / -{deleted}",
    "md_stats": "Store: {count} blobs, {mb:.1f} MB · events: {events}",
    "md_not_ready": (
        "Media baseline hasn't run yet — run 'Baseline Media Files' from the "
        "Tools → Note Version History menu first."
    ),
    "event_added": "Added",
    "event_modified": "Modified",
    "event_deleted": "Deleted",
    "media_baseline_prompt": (
        "Also back up your media files?\n\n"
        "{count} files (~{mb:.1f} MB) will be copied ONCE into the add-on's "
        "store; afterwards only changed files take extra space.\n\n"
        "You can skip this and run it later from the Tools menu."
    ),
    "media_baseline_resume_prompt": (
        "A previous media baseline was interrupted. Resume it now?\n\n"
        "{count} files (~{mb:.1f} MB) total."
    ),
    "media_baseline_skipped": (
        "Media baseline skipped — you can run it later from the Tools menu."
    ),
    "media_baseline_progress": "Backing up media…",
    "media_baseline_progress_label": "Media: {done} / {total} files",
    "media_baseline_done": "Media baseline complete ({count} files).",
    # Maintenance
    "menu_full_rescan": "Full Rescan",
    "rescan_needs_baseline": (
        "Full rescan is available after a full baseline "
        "(Tools → Baseline Entire Collection)."
    ),
    "rescan_progress": "Rescanning all notes…",
    "rescan_progress_label": "Rescan: {done} / {total} notes",
    "rescan_done": "Full rescan finished: {captured} change(s) captured.",
    "menu_compact": "Compact Database",
    "compact_progress": "Compacting version history…",
    "compact_done": "Compacted. Removed {blobs} unreferenced blob(s).",
}

_KO = {
    # General
    "addon_name": "버전 기록",
    # Tools menu
    "menu_root": "노트 버전 기록",
    "menu_about": "정보 / 통계…",
    # About dialog
    "about_title": "버전 기록 — 통계",
    "about_body": (
        "노트 버전: {notes}\n"
        "노트타입 버전: {notetypes}\n"
        "미디어 이벤트: {media}\n"
        "블롭 저장소: {blob_count}개 파일, {blob_mb:.1f} MB\n\n"
        "데이터베이스: {db_path}"
    ),
    "about_no_profile": "열려 있는 프로필이 없습니다.",
    # Errors
    "db_too_new": (
        "버전 기록 데이터베이스가 이 애드온보다 새로운 버전으로 만들어졌습니다. "
        "애드온을 업데이트해 주세요."
    ),
    "db_open_failed": "버전 기록 데이터베이스를 열 수 없습니다:\n{error}",
    # Capture pipeline
    "label_undo": "실행 취소: {label}",
    "label_redo": "다시 실행: {label}",
    "scan_failed": "버전 캡처에 실패했습니다:\n{error}",
    # Baseline wizard
    "baseline_intro_title": "버전 기록 — 전체 베이스라인",
    "baseline_intro": (
        "편집하는 노트는 이미 자동으로 기록됩니다. 여기서는 컬렉션 '전체'를 "
        "1회 베이스라인으로 저장해, 아직 편집하지 않은 노트도 나중에 되돌릴 수 "
        "있게 합니다 (예: 일괄 찾아바꾸기·동기화 후).\n\n"
        "노트: {notes}개 (텍스트 약 {mb:.1f} MB)\n"
        "노트타입: {notetypes}개\n\n"
        "백그라운드에서 실행되며 컬렉션은 읽기만 합니다. 지금 시작할까요?"
    ),
    "baseline_resume_prompt": (
        "이전 베이스라인 작업이 중단되었습니다. 이어서 진행할까요?\n\n"
        "노트: {notes}개 (텍스트 약 {mb:.1f} MB)\n"
        "노트타입: {notetypes}개"
    ),
    "baseline_progress": "베이스라인 저장 중…",
    "baseline_progress_label": "베이스라인: {done} / {total} 노트",
    "baseline_done": "버전 기록 베이스라인 완료 ({count}개 노트).",
    "baseline_postponed": "취소했습니다 — 전체 베이스라인은 도구 메뉴에서 언제든 실행할 수 있습니다.",
    "baseline_failed": "베이스라인 실패:\n{error}",
    # Browser / editor entry points
    "menu_note_history": "🕘 버전 기록…",
    "no_note_selected": "먼저 노트를 선택하세요.",
    "menu_snapshot_selected": "선택한 노트 {count}개 스냅샷",
    "snapshot_done": "스냅샷 저장됨 ({count}개 노트).",
    "editor_history_tip": "버전 기록 (스냅샷·복원)",
    "editor_unsaved_note": "먼저 노트를 저장하세요 (아직 id가 없습니다).",
    "no_profile_open": "열려 있는 프로필이 없습니다.",
    # History dialog
    "hd_title": "버전 기록 — 노트 {nid}",
    "hd_no_versions": "이 노트에 기록된 버전이 아직 없습니다.",
    "hd_view_only": "이 버전 내용만 보기 (비교 없음)",
    "hd_diff_vs_current": "현재 노트와 비교",
    "hd_diff_vs_previous": "이전 버전과 비교",
    "hd_restore_version": "이 버전으로 복원",
    "hd_restore_as_new": "새 노트로 복원…",
    "hd_restore_fields": "선택 필드만 복원",
    "hd_snapshot_now": "스냅샷 추가",
    "hd_close": "닫기",
    "hd_tags": "태그",
    "hd_deleted_banner": "이 버전은 노트의 '삭제'를 기록한 것입니다.",
    "hd_note_missing_banner": "이 노트는 컬렉션에 더 이상 존재하지 않습니다.",
    "origin_baseline": "베이스라인",
    "origin_auto": "자동",
    "origin_manual": "스냅샷",
    "origin_restore": "복원",
    # System-event timeline labels (stored as "@" sentinels, translated here)
    "@delete_note": "노트 삭제",
    "@undo_delete": "삭제 취소",
    "@full_rescan": "전체 재검사",
    "@delete_notetype": "노트타입 삭제",
    # Restore flows
    "undo_restore_note": "노트 버전 복원",
    "undo_restore_as_new": "새 노트로 복원",
    "confirm_restore": "현재 노트 내용을 {when} 버전으로 덮어쓸까요?",
    "no_fields_selected": "복원할 필드를 하나 이상 선택하세요.",
    "restore_done": "노트 버전을 복원했습니다 (Ctrl+Z로 취소 가능).",
    "restore_fields_done": "필드 복원됨: {fields}",
    "restore_skipped_fields": (
        "현재 노트타입에 없는 저장 필드는 건너뛰었습니다: {fields}"
    ),
    "restore_failed": "복원 실패:\n{error}",
    "restore_guid_mismatch": (
        "이 노트 id는 지금 '다른' 노트가 사용 중입니다 (원본이 삭제된 뒤 "
        "가져오기 등으로 id가 재사용됨). 현재 노트 보호를 위해 제자리 복원을 "
        "차단했습니다 — \"새 노트로 복원\"을 사용하세요."
    ),
    "restore_as_new_prompt": "원본 노트가 없습니다. 이 버전 내용을 '새 노트'로 복원할까요?",
    "restore_pick_deck": "어느 덱으로 복원할까요?",
    "restore_as_new_done": "새 노트로 복원했습니다 (Ctrl+Z로 취소 가능).",
    # Note type history dialog
    "menu_notetype_history": "노트타입 기록…",
    "clayout_history_button": "🕘 버전 기록…",
    "ntd_title": "버전 기록 — 노트타입",
    "ntd_pick": "노트타입:",
    "ntd_diff_vs_current": "현재 노트타입과 비교 (미저장 편집 포함)",
    "ntd_no_versions": "이 노트타입에 기록된 버전이 아직 없습니다.",
    "ntd_front": "앞면 템플릿",
    "ntd_back": "뒷면 템플릿",
    "ntd_css_tab": "CSS",
    "ntd_deleted_banner": "이 버전은 노트타입의 '삭제'를 기록한 것입니다.",
    "ntd_notetype_missing": "이 노트타입은 컬렉션에 더 이상 존재하지 않습니다.",
    "ntd_restore": "템플릿+CSS 복원",
    "ntd_confirm_restore": (
        "\"{name}\"의 현재 템플릿과 CSS를 {when} 버전으로 덮어쓸까요?\n\n"
        "(필드·학습 정보는 건드리지 않으며 전체 동기화를 강제하지 않습니다.)"
    ),
    "undo_restore_notetype": "노트타입 버전 복원",
    "ntd_restore_done": "템플릿+CSS를 복원했습니다 (Ctrl+Z로 취소 가능).",
    "ntd_mismatch_warning": (
        "복원 중 템플릿 이름 불일치가 있었습니다.\n"
        "적용됨: {applied}\n"
        "버전에만 있어 건너뜀: {missing_current}\n"
        "현재에만 있어 유지됨: {missing_stored}"
    ),
    "ntd_snapshot_done": "노트타입 스냅샷을 저장했습니다.",
    "ntd_deleted_suffix": "(삭제됨)",
    "ntd_loaded_into_editor": (
        "열려 있는 카드 유형 편집기에 이 버전을 불러왔습니다 — 미리보기를 "
        "확인하고 '저장'을 눌러 반영하세요 (저장하지 않고 닫으면 취소됩니다)."
    ),
    "ntd_editor_conflict": (
        "이 노트타입의 카드 유형 편집기가 열려 있는데 새로고침하지 못했습니다. "
        "편집기를 닫고 복원을 다시 시도하세요."
    ),
    # Media
    "menu_media_history": "미디어 기록…",
    "menu_resume_media_baseline": "미디어 베이스라인 만들기…",
    "menu_baseline_now": "전체 컬렉션 베이스라인 만들기…",
    "md_title": "버전 기록 — 미디어",
    "md_filter": "필터:",
    "md_scan_now": "지금 미디어 스캔",
    "md_restore": "이 버전으로 복원",
    "md_no_selection": "파일과 버전을 선택하세요.",
    "md_restore_confirm": "디스크의 \"{fname}\" 파일을 선택한 버전으로 덮어쓸까요?",
    "md_restore_done": "미디어 파일을 복원했습니다.",
    "md_restore_failed": "미디어 복원 실패:\n{error}",
    "md_scan_done": "미디어 스캔: +{added} / ~{modified} / -{deleted}",
    "md_stats": "저장소: {count}개 블롭, {mb:.1f} MB · 이벤트: {events}",
    "md_not_ready": (
        "미디어 베이스라인이 아직 실행되지 않았습니다 — 도구 → 노트 버전 기록 메뉴의 "
        "'미디어 베이스라인 만들기'를 먼저 실행하세요."
    ),
    "event_added": "추가",
    "event_modified": "수정",
    "event_deleted": "삭제",
    "media_baseline_prompt": (
        "미디어 파일도 백업할까요?\n\n"
        "{count}개 파일(약 {mb:.1f} MB)이 애드온 저장소로 '1회' 복사되며, "
        "이후에는 변경된 파일만 추가 용량을 차지합니다.\n\n"
        "건너뛰고 나중에 도구 메뉴에서 실행할 수도 있습니다."
    ),
    "media_baseline_resume_prompt": (
        "이전 미디어 베이스라인이 중단되었습니다. 이어서 진행할까요?\n\n"
        "전체 {count}개 파일 (약 {mb:.1f} MB)."
    ),
    "media_baseline_skipped": "미디어 베이스라인을 건너뛰었습니다 — 도구 메뉴에서 나중에 실행할 수 있습니다.",
    "media_baseline_progress": "미디어 백업 중…",
    "media_baseline_progress_label": "미디어: {done} / {total} 파일",
    "media_baseline_done": "미디어 베이스라인 완료 ({count}개 파일).",
    # Maintenance
    "menu_full_rescan": "전체 재검사",
    "rescan_needs_baseline": (
        "전체 재검사는 전체 베이스라인을 먼저 실행한 뒤 사용할 수 있습니다 "
        "(도구 → 전체 컬렉션 베이스라인 만들기)."
    ),
    "rescan_progress": "모든 노트 재검사 중…",
    "rescan_progress_label": "재검사: {done} / {total} 노트",
    "rescan_done": "전체 재검사 완료: 변경 {captured}건 캡처.",
    "menu_compact": "데이터베이스 압축",
    "compact_progress": "버전 기록 압축 중…",
    "compact_done": "압축 완료. 참조되지 않는 블롭 {blobs}개 제거.",
}

STRINGS: dict[str, dict[str, str]] = {"en": _EN, "ko": _KO}
