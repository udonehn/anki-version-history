# Version History — Configuration

[English](#english) · [한국어](#한국어)

## English

Open **Tools → Add-ons**, select **Version History - Notes and Note Types**, and
choose **Config**. Save valid JSON to apply it.

The settings cache and heartbeat timer refresh when you save. Settings that govern
capture affect subsequent work; they do not rewrite or delete existing history.
Retention is applied later by automatic maintenance, and a restart is recommended
after changing the UI language so the already-created Tools menu is relabeled.
Out-of-range integers are clamped to the documented range; invalid value types
fall back to safe defaults. Non-integer entries in `exclude_notetype_ids` are
ignored.

### Capture

- **`auto_capture`** (default `true`)

  Enables scans after relevant Anki operations, undo/redo, sync, heartbeat,
  profile-open catch-up, and unclean-shutdown healing. Turning it off also pauses
  automatic retention maintenance. It does not disable manual snapshots, the
  manual full baseline, **Check & Repair Missing History…**, history viewing, or
  restore. Changes made while it is off do not automatically acquire a recoverable
  pre-change version. Saving `false` does not force-cancel a scan that is already
  running. While off, the add-on neither advances nor discards its history scan
  marker/pending boundary. Consequently, the first automatic scan after
  re-enabling may store the final observed state reached during the off period as
  a new row, but it cannot reconstruct that period's pre-change or intermediate
  states.

- **`debounce_ms`** (default `1500`, range `100`–`60000`)

  Wait this many milliseconds after the most recent normal Anki operation before
  scanning. Rapid consecutive operations can therefore coalesce into one stored
  final state. Post-sync and heartbeat scans use their own scheduling.

- **`heartbeat_scan_minutes`** (default `5`, range `0`–`1440`)

  Periodically scan for changes that arrived without a useful normal operation
  hook, such as changes made by another add-on. `0` disables the heartbeat. A
  heartbeat can observe the resulting state; it cannot reconstruct an untracked
  pre-change state.

- **`exclude_notetype_ids`** (default `[]`)

  Integer note-type IDs whose notes are skipped by **automatic note capture**.
  This does not delete history already stored. It also does not exclude note-type
  template/CSS capture, manual snapshots, or the manually started full baseline.

### Retention

Retention applies only to unpinned, non-deletion **automatic note** rows. It does
not prune note-type versions, manual snapshots, baseline rows, restore rows,
deletion markers, or pinned automatic rows. The newest automatic row for each
logical note is always kept.

Maintenance becomes due at most once every 24 hours and runs during a later
automatic note scan. Saving Config does not prune immediately. Turning automatic
capture off also pauses automatic maintenance; it resumes after automatic capture
is enabled again. **Reclaim Database Space…** vacuum-compacts pages that are
already free; it does not choose extra versions to delete.

- **`retention.max_auto_versions_per_note`** (default `100`, range `1`–`100000`)

  Maximum unpinned automatic versions retained per logical note, subject also to
  the age rule. Pinned versions do not count toward this limit.

- **`retention.max_age_days`** (default `180`, range `0`–`36500`)

  Prune eligible automatic note versions older than this age. `0` disables the age
  rule, but the per-note count rule still applies.

- **`retention.media_max_age_days`** (default `0`, range `0`–`36500`)

  Reserved for media history. Media capture is disabled in this release, so this
  setting currently has no effect. `0` would disable age-based media pruning when
  that feature is enabled.

### Language

- **`language`** (default `"auto"`)

  `"auto"` follows Anki's language when supported. Use `"en"` or `"ko"` to force
  English or Korean. Unsupported values fall back to English. Newly opened dialogs
  use the saved language immediately, but restart Anki to reliably update the
  existing Tools menu and actions.

### Media keys (inactive in this release)

Media history is disabled by the release feature flag. None of these keys causes
media files to be scanned or copied in this release:

- **`capture_media`** (default `true`)
- **`media_scan_on_profile_open`** (default `true`)
- **`media_scan_on_profile_close`** (default `false`)

They are retained for forward compatibility.

## 한국어

**도구 → 부가기능**을 열고 **Version History - Notes and Note Types**를 선택한 뒤
**설정**을 누르세요. 올바른 JSON을 저장하면 적용됩니다.

저장할 때 설정 캐시와 heartbeat 타이머가 갱신됩니다. 캡처 관련 설정은 이후 작업부터
적용되며 기존 기록을 다시 쓰거나 삭제하지 않습니다. 보존 설정은 이후 자동 유지보수 때
적용됩니다. 언어를 바꾼 뒤에는 이미 만들어진 도구 메뉴까지 다시 표시되도록 Anki를
재시작하는 것을 권장합니다.
범위를 벗어난 정수는 문서의 범위로 제한되며, 값의 자료형이 잘못되면 안전한 기본값으로
대체됩니다. `exclude_notetype_ids`의 정수가 아닌 항목은 무시됩니다.

### 캡처

- **`auto_capture`** (기본값 `true`)

  관련 Anki 작업, 실행 취소/다시 실행, 동기화, heartbeat, 프로필 시작 후 따라잡기,
  비정상 종료 복구 검사를 활성화합니다. 끄면 자동 보존 유지보수도 일시 중지됩니다. 꺼도
  수동 스냅샷, 수동 전체 베이스라인, **기록 누락 검사·복구…**, 기록 열람, 복원은 사용할
  수 있습니다. 꺼 둔 동안 발생한 변경에는 복원 가능한 변경 전 버전이 자동으로 생기지
  않습니다. `false`를 저장해도 이미 실행 중인 검사를 강제로 취소하지는 않습니다. 꺼진
  동안에는 기록 검사 marker/pending boundary를 전진시키거나 버리지 않습니다. 따라서
  다시 켠 뒤 첫 자동 검사에서 off 기간에 도달한 최종 관찰 상태가 새 행으로 저장될 수
  있지만, 그 기간의 변경 전 상태나 중간 상태는 재구성할 수 없습니다.

- **`debounce_ms`** (기본값 `1500`, 범위 `100`–`60000`)

  일반 Anki 작업이 마지막으로 발생한 뒤 검사하기까지 기다릴 밀리초입니다. 따라서 빠른
  연속 작업은 하나의 최종 저장 상태로 합쳐질 수 있습니다. 동기화 후 검사와 heartbeat
  검사는 별도 일정으로 실행됩니다.

- **`heartbeat_scan_minutes`** (기본값 `5`, 범위 `0`–`1440`)

  다른 애드온이 만든 변경처럼 일반 작업 훅으로 알기 어려운 변경을 주기적으로 검사합니다.
  `0`이면 heartbeat를 끕니다. heartbeat는 결과 상태를 관찰할 수 있지만, 캡처된 적 없는
  변경 전 상태를 재구성할 수는 없습니다.

- **`exclude_notetype_ids`** (기본값 `[]`)

  해당 노트타입의 노트를 **자동 노트 캡처**에서 제외할 정수 ID 목록입니다. 이미 저장된
  기록은 삭제하지 않습니다. 노트타입 템플릿/CSS 캡처, 수동 스냅샷, 사용자가 실행하는
  전체 베이스라인에도 적용되지 않습니다.

### 보존

보존 설정은 고정하지 않았고 삭제 표시가 아닌 **자동 노트** 행에만 적용됩니다. 노트타입
버전, 수동 스냅샷, 베이스라인 행, 복원 행, 삭제 표시, 고정 자동 행은 정리하지 않습니다.
논리 노트별 최신 자동 행도 항상 남깁니다.

유지보수는 마지막 실행 후 24시간이 지나야 다시 실행 대상이 되며, 이후 자동 노트 검사에서
실행됩니다. 설정을 저장한다고 즉시 정리되지는 않습니다. 자동 캡처를 끄면 자동
유지보수도 일시 중지되며, 자동 캡처를 다시 켠 뒤 재개됩니다. **DB 여유 공간 회수…**는
이미 비어 있는 페이지를 vacuum 압축할 뿐 추가로 삭제할 버전을 고르지 않습니다.

- **`retention.max_auto_versions_per_note`** (기본값 `100`, 범위
  `1`–`100000`)

  논리 노트별로 유지할 고정하지 않은 자동 버전의 최대 개수입니다. 아래 기간 규칙도 함께
  적용됩니다. 고정 버전은 이 제한에 포함되지 않습니다.

- **`retention.max_age_days`** (기본값 `180`, 범위 `0`–`36500`)

  정리 대상 자동 노트 버전 중 이 기간보다 오래된 행을 삭제합니다. `0`이면 기간 규칙만
  끄며, 노트별 개수 규칙은 계속 적용됩니다.

- **`retention.media_max_age_days`** (기본값 `0`, 범위 `0`–`36500`)

  미디어 기록용 예약 설정입니다. 이번 릴리스에서는 미디어 캡처가 비활성화되어 아무
  효과가 없습니다. 해당 기능이 활성화된 뒤 `0`은 미디어 기간 정리를 끄는 값입니다.

### 언어

- **`language`** (기본값 `"auto"`)

  `"auto"`는 지원되는 경우 Anki 언어를 따릅니다. `"en"` 또는 `"ko"`로 영어·한국어를
  강제할 수 있습니다. 지원하지 않는 문자열은 영어로 대체됩니다. 새로 여는 대화상자에는
  저장한 언어가 즉시 적용되지만, 기존 도구 메뉴와 작업을 확실히 갱신하려면 Anki를
  재시작하세요.

### 미디어 키(이번 릴리스에서는 비활성)

릴리스 기능 플래그로 미디어 기록이 꺼져 있습니다. 이번 릴리스에서 아래 키는 미디어 파일을
검사하거나 복사하지 않습니다.

- **`capture_media`** (기본값 `true`)
- **`media_scan_on_profile_open`** (기본값 `true`)
- **`media_scan_on_profile_close`** (기본값 `false`)

향후 호환성을 위해 남겨 둔 설정입니다.

## Full default configuration / 전체 기본 설정

```json
{
    "auto_capture": true,
    "debounce_ms": 1500,
    "heartbeat_scan_minutes": 5,
    "capture_media": true,
    "media_scan_on_profile_open": true,
    "media_scan_on_profile_close": false,
    "retention": {
        "max_auto_versions_per_note": 100,
        "max_age_days": 180,
        "media_max_age_days": 0
    },
    "exclude_notetype_ids": [],
    "language": "auto"
}
```
