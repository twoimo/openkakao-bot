#!/bin/sh
# Install the built Tauri app and cut the menu LaunchAgent over to Alden.
set -eu
umask 077

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
APP_NAME="Alden.app"
APP_EXECUTABLE="openkakao-alden-desktop"
JARVIS_APP_NAME="OpenKakao Jarvis.app"
JARVIS_EXECUTABLE="openkakao-jarvis-desktop"
BUNDLE_ROOT="$ROOT/desktop/src-tauri/target"
TEMPLATE="$ROOT/desktop/launchd/com.openkakao.alden.desktop.plist.example"

APPLICATIONS_DIR=${OPENKAKAO_APPLICATIONS_DIR:-/Applications}
LAUNCH_AGENTS_DIR=${OPENKAKAO_LAUNCH_AGENTS_DIR:-"$HOME/Library/LaunchAgents"}
BACKUP_BASE=${OPENKAKAO_ALDEN_BACKUP_DIR:-"$HOME/Library/Application Support/openkakao/install-backups/alden-desktop"}
LAUNCHCTL=${OPENKAKAO_LAUNCHCTL:-/bin/launchctl}
DITTO=${OPENKAKAO_DITTO:-/usr/bin/ditto}
PLISTBUDDY=${OPENKAKAO_PLISTBUDDY:-/usr/libexec/PlistBuddy}
PLUTIL=${OPENKAKAO_PLUTIL:-/usr/bin/plutil}
PS=${OPENKAKAO_PS:-/bin/ps}
PGREP=${OPENKAKAO_PGREP:-/usr/bin/pgrep}
LSOF=${OPENKAKAO_LSOF:-/usr/sbin/lsof}

RUNTIME_PYTHON="$HOME/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11"
if [ -n "${OPENKAKAO_ALDEN_RUNTIME_PYTHON_TEST_ONLY:-}" ]; then
  if [ "${OPENKAKAO_ALDEN_INSTALLER_TEST_ONLY:-0}" != "1" ]; then
    echo "install-alden-desktop: test-only runtime override requires OPENKAKAO_ALDEN_INSTALLER_TEST_ONLY=1" >&2
    exit 2
  fi
  RUNTIME_PYTHON=$OPENKAKAO_ALDEN_RUNTIME_PYTHON_TEST_ONLY
fi

LEGACY_LABEL="com.openkakao.auto-reply.menu"
JARVIS_LABEL="com.openkakao.jarvis.desktop"
ALDEN_LABEL="com.openkakao.alden.desktop"
UID_NOW=$(id -u)
DOMAIN="gui/$UID_NOW"
LEGACY_SERVICE="$DOMAIN/$LEGACY_LABEL"
JARVIS_SERVICE="$DOMAIN/$JARVIS_LABEL"
ALDEN_SERVICE="$DOMAIN/$ALDEN_LABEL"
LEGACY_PLIST="$LAUNCH_AGENTS_DIR/$LEGACY_LABEL.plist"
JARVIS_PLIST="$LAUNCH_AGENTS_DIR/$JARVIS_LABEL.plist"
ALDEN_PLIST="$LAUNCH_AGENTS_DIR/$ALDEN_LABEL.plist"
TARGET_APP="$APPLICATIONS_DIR/$APP_NAME"
TARGET_BIN="$TARGET_APP/Contents/MacOS/$APP_EXECUTABLE"
JARVIS_APP="$APPLICATIONS_DIR/$JARVIS_APP_NAME"
JARVIS_BIN="$JARVIS_APP/Contents/MacOS/$JARVIS_EXECUTABLE"

SOURCE_APP=${OPENKAKAO_ALDEN_APP_SOURCE:-}
if [ -z "$SOURCE_APP" ]; then
  DEFAULT_APP="$BUNDLE_ROOT/release/bundle/macos/$APP_NAME"
  if [ -d "$DEFAULT_APP" ]; then
    SOURCE_APP=$DEFAULT_APP
  elif [ -d "$BUNDLE_ROOT" ]; then
    SOURCE_APP=$(find "$BUNDLE_ROOT" -type d \
      -path "*/release/bundle/macos/$APP_NAME" -prune -print |
      /usr/bin/sed -n '1p')
  fi
fi

if [ -z "$SOURCE_APP" ] || [ ! -d "$SOURCE_APP" ]; then
  echo "install-alden-desktop: release Tauri bundle was not found" >&2
  echo "run scripts/build-alden-desktop.sh first" >&2
  exit 2
fi
if [ ! -x "$SOURCE_APP/Contents/MacOS/$APP_EXECUTABLE" ]; then
  echo "install-alden-desktop: bundle executable is missing: $SOURCE_APP" >&2
  exit 2
fi
if [ ! -f "$TEMPLATE" ]; then
  echo "install-alden-desktop: LaunchAgent template is missing: $TEMPLATE" >&2
  exit 2
fi
for tool in "$LAUNCHCTL" "$DITTO" "$PLISTBUDDY" "$PLUTIL" "$PS"; do
  if [ ! -x "$tool" ]; then
    echo "install-alden-desktop: required tool is not executable: $tool" >&2
    exit 2
  fi
done
if [ ! -d "$APPLICATIONS_DIR" ] || [ ! -w "$APPLICATIONS_DIR" ]; then
  echo "install-alden-desktop: cannot write to $APPLICATIONS_DIR" >&2
  exit 2
fi

runtime_preflight_fail() {
  echo "install-alden-desktop: CPython 3.11 runtime preflight failed: $1" >&2
  exit 2
}

runtime_path_component=$RUNTIME_PYTHON
case "$runtime_path_component" in
  /*)
    ;;
  *)
    runtime_preflight_fail "runtime path is not absolute"
    ;;
esac
case "$runtime_path_component" in
  */./*|*/../*|*/.|*/..)
    runtime_preflight_fail "runtime path contains dot components"
    ;;
esac

runtime_path_is_leaf=1
while :; do
  if [ -L "$runtime_path_component" ]; then
    runtime_preflight_fail "symlink path component: $runtime_path_component"
  fi

  if [ "$runtime_path_is_leaf" -eq 1 ]; then
    if [ ! -f "$runtime_path_component" ] || [ ! -s "$runtime_path_component" ] || \
      [ ! -r "$runtime_path_component" ] || [ ! -x "$runtime_path_component" ]; then
      runtime_preflight_fail "interpreter is missing, empty, unreadable, or not executable: $runtime_path_component"
    fi
    runtime_path_is_leaf=0
  elif [ ! -d "$runtime_path_component" ] || [ ! -x "$runtime_path_component" ]; then
    runtime_preflight_fail "parent is missing, not a directory, or not searchable: $runtime_path_component"
  fi

  if [ "$runtime_path_component" = "/" ]; then
    break
  fi
  runtime_path_parent=${runtime_path_component%/*}
  if [ -z "$runtime_path_parent" ]; then
    runtime_path_parent=/
  fi
  if [ "$runtime_path_parent" = "$runtime_path_component" ]; then
    runtime_preflight_fail "could not walk runtime path"
  fi
  runtime_path_component=$runtime_path_parent
done

# Match the installed bridge contract without importing site/user code. Require
# both exit 0 and exact Python-produced stdout; a no-op executable that merely
# exits successfully must not satisfy the probe. Limit stdout at the OS level so
# a damaged executable cannot grow the probe file without bound.
RUNTIME_PROBE_SENTINEL=ALDEN_CPYTHON_3_11_OK
runtime_probe_output=$(/usr/bin/mktemp "${TMPDIR:-/tmp}/openkakao-alden-python-probe.XXXXXX") || \
  runtime_preflight_fail "could not create bounded probe output"
(
  ulimit -f 1
  exec "$RUNTIME_PYTHON" -I -S -c \
    'import sys; ok=sys.implementation.name=="cpython" and sys.version_info[:2]==(3,11); print("ALDEN_CPYTHON_3_11_OK") if ok else None; raise SystemExit(0 if ok else 3)' \
    >"$runtime_probe_output" 2>/dev/null
) &
runtime_probe_pid=$!
runtime_probe_attempt=0
while /bin/kill -0 "$runtime_probe_pid" 2>/dev/null; do
  runtime_probe_attempt=$((runtime_probe_attempt + 1))
  if [ "$runtime_probe_attempt" -ge 10 ]; then
    /bin/kill "$runtime_probe_pid" 2>/dev/null || true
    /bin/sleep 0.1
    /bin/kill -9 "$runtime_probe_pid" 2>/dev/null || true
    wait "$runtime_probe_pid" 2>/dev/null || true
    rm -f "$runtime_probe_output"
    runtime_preflight_fail "version probe timed out"
  fi
  /bin/sleep 0.1
done
runtime_probe_status=0
wait "$runtime_probe_pid" || runtime_probe_status=$?
if [ "$runtime_probe_status" -ne 0 ]; then
  rm -f "$runtime_probe_output"
  runtime_preflight_fail "interpreter is not isolated CPython 3.11"
fi
if ! printf '%s\n' "$RUNTIME_PROBE_SENTINEL" | /usr/bin/cmp -s - "$runtime_probe_output"; then
  rm -f "$runtime_probe_output"
  runtime_preflight_fail "interpreter did not emit exact CPython 3.11 sentinel"
fi
rm -f "$runtime_probe_output"

mkdir -p "$LAUNCH_AGENTS_DIR" "$BACKUP_BASE"
STAMP=$(/bin/date +%Y%m%dT%H%M%S)
BACKUP_DIR="$BACKUP_BASE/$STAMP-$$"
mkdir "$BACKUP_DIR"

# Preserve launchd readback and all three plists before stopping any job.
LEGACY_LOADED=0
if "$LAUNCHCTL" print "$LEGACY_SERVICE" \
  >"$BACKUP_DIR/$LEGACY_LABEL.launchctl.txt" 2>&1; then
  LEGACY_LOADED=1
fi
if [ -f "$LEGACY_PLIST" ]; then
  cp -p "$LEGACY_PLIST" "$BACKUP_DIR/$LEGACY_LABEL.plist"
else
  printf '%s\n' "not present: $LEGACY_PLIST" \
    >"$BACKUP_DIR/$LEGACY_LABEL.plist.missing"
fi

JARVIS_LOADED=0
HAD_JARVIS_PLIST=0
if "$LAUNCHCTL" print "$JARVIS_SERVICE" \
  >"$BACKUP_DIR/$JARVIS_LABEL.launchctl.txt" 2>&1; then
  JARVIS_LOADED=1
fi
if [ -f "$JARVIS_PLIST" ]; then
  HAD_JARVIS_PLIST=1
  cp -p "$JARVIS_PLIST" "$BACKUP_DIR/$JARVIS_LABEL.plist"
else
  printf '%s\n' "not present: $JARVIS_PLIST" \
    >"$BACKUP_DIR/$JARVIS_LABEL.plist.missing"
fi

ALDEN_LOADED=0
HAD_PREVIOUS_ALDEN_PLIST=0
if "$LAUNCHCTL" print "$ALDEN_SERVICE" \
  >"$BACKUP_DIR/$ALDEN_LABEL.launchctl.txt" 2>&1; then
  ALDEN_LOADED=1
fi
if [ -f "$ALDEN_PLIST" ]; then
  HAD_PREVIOUS_ALDEN_PLIST=1
  cp -p "$ALDEN_PLIST" "$BACKUP_DIR/$ALDEN_LABEL.plist"
else
  printf '%s\n' "not present: $ALDEN_PLIST" \
    >"$BACKUP_DIR/$ALDEN_LABEL.plist.missing"
fi

STAGING_DIR=$(mktemp -d "$APPLICATIONS_DIR/.openkakao-alden.install.XXXXXX")
STAGED_APP="$STAGING_DIR/$APP_NAME"
PLIST_STAGE=$(mktemp "$LAUNCH_AGENTS_DIR/.$ALDEN_LABEL.plist.XXXXXX")
PREVIOUS_APP=""
PREVIOUS_APP_MOVED=0
APP_ACTIVATED=0
ALDEN_PLIST_INSTALLED=0
ALDEN_BOOTSTRAPPED=0
ROLLBACK_ATTEMPTED=0
RUNTIME_TOUCHED=0

cleanup() {
  if [ -n "$PLIST_STAGE" ] && [ -f "$PLIST_STAGE" ]; then
    rm -f "$PLIST_STAGE"
  fi
  if [ -n "$STAGING_DIR" ] && [ -d "$STAGING_DIR" ]; then
    rm -rf "$STAGING_DIR"
  fi
}

signal_exit() {
  signal_status=$1
  cleanup
  if [ "$RUNTIME_TOUCHED" -eq 1 ] || [ "$APP_ACTIVATED" -eq 1 ] || \
    [ "$ALDEN_PLIST_INSTALLED" -eq 1 ] || [ "$ALDEN_BOOTSTRAPPED" -eq 1 ]; then
    perform_rollback "signal exit $signal_status"
  fi
  exit "$signal_status"
}

trap cleanup EXIT
trap 'signal_exit 129' HUP
trap 'signal_exit 130' INT
trap 'signal_exit 143' TERM

wait_for_absent() {
  wait_service=$1
  wait_log=$2
  wait_attempt=1
  : >"$wait_log"

  while [ "$wait_attempt" -le 10 ]; do
    printf 'attempt %s/10\n' "$wait_attempt" >>"$wait_log"
    if ! "$LAUNCHCTL" print "$wait_service" >>"$wait_log" 2>&1; then
      return 0
    fi
    if [ "$wait_attempt" -lt 10 ]; then
      /bin/sleep 0.5
    fi
    wait_attempt=$((wait_attempt + 1))
  done
  return 1
}

wait_for_pid() {
  wait_service=$1
  wait_log=$2
  wait_attempt=1

  while [ "$wait_attempt" -le 10 ]; do
    if "$LAUNCHCTL" print "$wait_service" >"$wait_log" 2>&1; then
      wait_pid=$(/usr/bin/awk '
        /^[[:space:]]*pid = [0-9]+/ { print $3; exit }
      ' "$wait_log")
      case "$wait_pid" in
        ''|*[!0-9]*)
          ;;
        *)
          printf '%s\n' "$wait_pid"
          return 0
          ;;
      esac
    fi
    if [ "$wait_attempt" -lt 10 ]; then
      /bin/sleep 0.5
    fi
    wait_attempt=$((wait_attempt + 1))
  done
  return 1
}

list_matching_pids() {
  match_executable=$1
  candidate_pids=""
  use_ps=0

  if [ -x "$PGREP" ]; then
    pgrep_status=0
    pgrep_output=$("$PGREP" -f "$match_executable" 2>/dev/null) || pgrep_status=$?
    case "$pgrep_status" in
      0)
        candidate_pids=$(printf '%s\n' "$pgrep_output" |
          /usr/bin/awk '/^[[:space:]]*[0-9]+[[:space:]]*$/ { print $1 }')
        if [ -z "$candidate_pids" ]; then
          return 2
        fi
        ;;
      1)
        candidate_pids=""
        ;;
      2|3)
        use_ps=1
        ;;
      *)
        use_ps=1
        ;;
    esac
  else
    use_ps=1
  fi

  if [ "$use_ps" -eq 1 ]; then
    ps_status=0
    ps_output=$("$PS" -axo pid=,command= 2>/dev/null) || ps_status=$?
    if [ "$ps_status" -ne 0 ]; then
      return 2
    fi

    ps_parse_status=0
    candidate_pids=$(printf '%s\n' "$ps_output" |
      /usr/bin/awk -v executable="$match_executable" '
        BEGIN { saw_pid = 0 }
        $1 ~ /^[0-9]+$/ {
          saw_pid = 1
          pid = $1
          $1 = ""
          if ($0 ~ "(^|[[:space:]/])" executable "([[:space:]]|$)") {
            print pid
          }
        }
        END {
          if (!saw_pid) {
            exit 2
          }
        }
      ') || ps_parse_status=$?
    if [ "$ps_parse_status" -ne 0 ]; then
      return 2
    fi
  fi

  # A full-command match can be a diagnostic process, or a process that exited
  # between pgrep and readback. Only an executable identity proves an app PID.
  # An unresolved live process still blocks the cutover; it is never ignored.
  verified_pids=""
  for candidate_pid in $candidate_pids; do
    if candidate_path=$(process_executable_path "$candidate_pid"); then
      if [ "${candidate_path##*/}" = "$match_executable" ]; then
        verified_pids="$verified_pids $candidate_pid"
      fi
      continue
    fi
    candidate_status=0
    candidate_readback=$("$PS" -p "$candidate_pid" -o pid= 2>/dev/null) || \
      candidate_status=$?
    if [ "$candidate_status" -eq 1 ] && [ -z "$candidate_readback" ]; then
      continue
    fi
    return 2
  done
  candidate_pids=$(printf '%s\n' "$verified_pids" |
    /usr/bin/awk '{ for (i = 1; i <= NF; i++) if (!seen[$i]++) print $i }')
  printf '%s\n' "$candidate_pids"
}

list_live_app_pids() {
  list_matching_pids "$APP_EXECUTABLE"
}

list_jarvis_pids() {
  list_matching_pids "$JARVIS_EXECUTABLE"
}

process_executable_path() {
  process_pid=$1
  process_path=""

  if [ -x "$LSOF" ]; then
    process_path=$("$LSOF" -p "$process_pid" -a -d txt -Fn 2>/dev/null |
      /usr/bin/sed -n 's/^n//p' | /usr/bin/sed -n '1p' || true)
  fi
  if [ -z "$process_path" ]; then
    process_path=$("$PS" -p "$process_pid" -o comm= 2>/dev/null |
      /usr/bin/sed -n '1p' || true)
  fi
  case "$process_path" in
    /*)
      printf '%s\n' "$process_path"
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

reported_app_path() {
  reported_pid=$1
  if reported_path=$(process_executable_path "$reported_pid"); then
    printf '%s\n' "$reported_path"
  else
    printf '%s\n' "(unresolved)"
  fi
}

launchd_pid_from_readback() {
  readback_file=$1
  /usr/bin/awk '
    /^[[:space:]]*pid = [0-9]+/ { print $3; exit }
  ' "$readback_file"
}

verify_jarvis_plist() {
  if [ ! -f "$JARVIS_PLIST" ]; then
    if [ "$JARVIS_LOADED" -eq 1 ]; then
      echo "install-alden-desktop: loaded Jarvis service has no restorable plist" >&2
      return 1
    fi
    return 0
  fi

  jarvis_plist_label_status=0
  jarvis_plist_label=$("$PLISTBUDDY" -c "Print :Label" "$JARVIS_PLIST" 2>/dev/null) || \
    jarvis_plist_label_status=$?
  jarvis_plist_program_status=0
  jarvis_plist_program=$("$PLISTBUDDY" -c "Print :ProgramArguments:0" \
    "$JARVIS_PLIST" 2>/dev/null) || jarvis_plist_program_status=$?
  if [ "$jarvis_plist_label_status" -ne 0 ] || \
    [ "$jarvis_plist_program_status" -ne 0 ] || \
    [ "$jarvis_plist_label" != "$JARVIS_LABEL" ] || \
    [ "$jarvis_plist_program" != "$JARVIS_BIN" ]; then
    echo "install-alden-desktop: Jarvis plist ownership could not be verified" >&2
    return 1
  fi
  return 0
}

verify_jarvis_preflight() {
  if [ "$HAD_JARVIS_PLIST" -eq 1 ]; then
    if [ ! -f "$JARVIS_PLIST" ] || \
      ! /usr/bin/cmp -s "$BACKUP_DIR/$JARVIS_LABEL.plist" "$JARVIS_PLIST"; then
      echo "install-alden-desktop: Jarvis plist changed after backup" >&2
      return 1
    fi
  elif [ -e "$JARVIS_PLIST" ]; then
    echo "install-alden-desktop: Jarvis plist appeared after backup" >&2
    return 1
  fi

  if ! verify_jarvis_plist; then
    return 1
  fi

  jarvis_preflight_readback="$BACKUP_DIR/$JARVIS_LABEL.pre-cutover.launchctl.txt"
  jarvis_now_loaded=0
  if "$LAUNCHCTL" print "$JARVIS_SERVICE" \
    >"$jarvis_preflight_readback" 2>&1; then
    jarvis_now_loaded=1
  fi
  if [ "$jarvis_now_loaded" -ne "$JARVIS_LOADED" ]; then
    echo "install-alden-desktop: Jarvis service state changed after backup" >&2
    return 1
  fi

  jarvis_pids_status=0
  jarvis_pids=$(list_jarvis_pids) || jarvis_pids_status=$?
  if [ "$jarvis_pids_status" -ne 0 ]; then
    echo "install-alden-desktop: cannot verify Jarvis process ownership; process enumeration is unknown" >&2
    return 1
  fi

  if [ "$JARVIS_LOADED" -eq 0 ]; then
    if [ -z "$jarvis_pids" ]; then
      return 0
    fi
    echo "install-alden-desktop: stray Jarvis process detected before cutover" >&2
    for jarvis_stray_pid in $jarvis_pids; do
      jarvis_stray_path=$(reported_app_path "$jarvis_stray_pid")
      printf 'install-alden-desktop: Jarvis pid %s reported path: %s\n' \
        "$jarvis_stray_pid" "$jarvis_stray_path" >&2
    done
    return 1
  fi

  if [ ! -x "$JARVIS_BIN" ]; then
    echo "install-alden-desktop: loaded Jarvis executable is missing" >&2
    return 1
  fi
  JARVIS_PID=$(launchd_pid_from_readback "$jarvis_preflight_readback")
  case "$JARVIS_PID" in
    ''|*[!0-9]*)
      echo "install-alden-desktop: Jarvis service pid could not be verified" >&2
      return 1
      ;;
  esac
  if ! printf '%s\n' "$jarvis_pids" |
    /usr/bin/awk -v service_pid="$JARVIS_PID" \
      '$0 == service_pid { found = 1 } END { exit found ? 0 : 1 }'; then
    echo "install-alden-desktop: Jarvis service pid is not a live Jarvis process" >&2
    return 1
  fi
  if ! jarvis_service_path=$(process_executable_path "$JARVIS_PID"); then
    echo "install-alden-desktop: Jarvis service executable path is unresolved" >&2
    return 1
  fi
  if [ "$jarvis_service_path" != "$JARVIS_BIN" ]; then
    printf 'install-alden-desktop: Jarvis service path mismatch: %s\n' \
      "$jarvis_service_path" >&2
    return 1
  fi

  jarvis_extra_pids=$(printf '%s\n' "$jarvis_pids" |
    /usr/bin/awk -v service_pid="$JARVIS_PID" '
      /^[0-9]+$/ && $0 != service_pid && !seen[$0]++ { print $0 }
    ')
  if [ -n "$jarvis_extra_pids" ]; then
    echo "install-alden-desktop: stray Jarvis process detected before cutover" >&2
    for jarvis_stray_pid in $jarvis_extra_pids; do
      jarvis_stray_path=$(reported_app_path "$jarvis_stray_pid")
      printf 'install-alden-desktop: Jarvis pid %s reported path: %s\n' \
        "$jarvis_stray_pid" "$jarvis_stray_path" >&2
    done
    return 1
  fi
  return 0
}

wait_for_jarvis_absent() {
  wait_log=$1
  wait_attempt=1
  : >"$wait_log"

  while [ "$wait_attempt" -le 10 ]; do
    printf 'attempt %s/10\n' "$wait_attempt" >>"$wait_log"
    jarvis_wait_status=0
    jarvis_wait_pids=$(list_jarvis_pids) || jarvis_wait_status=$?
    if [ "$jarvis_wait_status" -ne 0 ]; then
      printf '%s\n' "process enumeration unknown" >>"$wait_log"
      return 2
    fi
    if [ -z "$jarvis_wait_pids" ]; then
      return 0
    fi
    printf 'live pids: %s\n' "$jarvis_wait_pids" >>"$wait_log"
    if [ "$wait_attempt" -lt 10 ]; then
      /bin/sleep 0.5
    fi
    wait_attempt=$((wait_attempt + 1))
  done
  return 1
}

check_no_jarvis_processes() {
  guard_jarvis_status=0
  guard_jarvis_pids=$(list_jarvis_pids) || guard_jarvis_status=$?
  if [ "$guard_jarvis_status" -ne 0 ]; then
    echo "install-alden-desktop: cannot prove the old Jarvis process is absent; process enumeration is unknown" >&2
    return 2
  fi
  if [ -z "$guard_jarvis_pids" ]; then
    return 0
  fi

  echo "install-alden-desktop: old Jarvis process detected after cutover" >&2
  for guard_jarvis_pid in $guard_jarvis_pids; do
    guard_jarvis_path=$(reported_app_path "$guard_jarvis_pid")
    printf 'install-alden-desktop: Jarvis pid %s reported path: %s\n' \
      "$guard_jarvis_pid" "$guard_jarvis_path" >&2
  done
  return 1
}

check_duplicate_guard() {
  guard_launchd_pid=$1
  live_app_pids=""
  guard_attempt=1

  jarvis_guard_status=0
  check_no_jarvis_processes || jarvis_guard_status=$?
  if [ "$jarvis_guard_status" -ne 0 ]; then
    return "$jarvis_guard_status"
  fi

  # A pid reported by launchd is not proof that the app survived activation.
  # Require that pid to be a live app process before trusting the cutover, with
  # a short bounded wait for the exec window right after kickstart.
  while :; do
    if ! live_app_pids=$(list_live_app_pids); then
      echo "install-alden-desktop: cannot prove there is no duplicate instance; process enumeration is unknown" >&2
      return 2
    fi
    if printf '%s\n' "$live_app_pids" |
      /usr/bin/awk -v launchd_pid="$guard_launchd_pid" \
        '$0 == launchd_pid { found = 1 } END { exit found ? 0 : 1 }'; then
      break
    fi
    if [ "$guard_attempt" -ge 5 ]; then
      printf 'install-alden-desktop: launchd pid %s is not a live app process\n' \
        "$guard_launchd_pid" >&2
      return 1
    fi
    guard_attempt=$((guard_attempt + 1))
    /bin/sleep 0.2
  done

  stray_pids=$(printf '%s\n' "$live_app_pids" |
    /usr/bin/awk -v launchd_pid="$guard_launchd_pid" -v installer_pid="$$" '
      /^[0-9]+$/ && $0 != launchd_pid && $0 != installer_pid && !seen[$0]++ {
        print $0
      }
    ')
  if [ -z "$stray_pids" ]; then
    return 0
  fi

  echo "install-alden-desktop: stray Alden process detected" >&2
  for stray_pid in $stray_pids; do
    stray_path=$(reported_app_path "$stray_pid")
    printf 'install-alden-desktop: stray pid %s reported path: %s\n' \
      "$stray_pid" "$stray_path" >&2
  done
  return 1
}

perform_rollback() {
  rollback_reason=$1

  if [ "$ROLLBACK_ATTEMPTED" -eq 1 ]; then
    return 0
  fi
  ROLLBACK_ATTEMPTED=1
  trap '' HUP INT TERM

  rollback_target_action="unchanged"
  rollback_plist_action="unchanged"
  rollback_alden_runtime_action="not previously loaded"
  rollback_legacy_plist_action="unchanged"
  rollback_legacy_runtime_action="not previously loaded"
  rollback_jarvis_plist_action="unchanged"
  rollback_jarvis_runtime_action="not previously loaded"
  rollback_artifact="$BACKUP_DIR/$ALDEN_LABEL.installed.txt"

  printf 'install-alden-desktop: %s; rollback was attempted\n' \
    "$rollback_reason" >&2

  if [ -e "$rollback_artifact" ]; then
    if rm -f "$rollback_artifact" && [ ! -e "$rollback_artifact" ]; then
      echo "install-alden-desktop: rollback installed artifact: removed" >&2
    else
      echo "install-alden-desktop: rollback installed artifact: removal failed" >&2
    fi
  fi

  if [ "$ALDEN_BOOTSTRAPPED" -eq 1 ]; then
    if "$LAUNCHCTL" bootout "$ALDEN_SERVICE" >/dev/null 2>&1; then
      echo "install-alden-desktop: rollback active Alden LaunchAgent: bootout succeeded" >&2
    else
      echo "install-alden-desktop: rollback active Alden LaunchAgent: bootout failed" >&2
    fi
  fi

  if [ "$APP_ACTIVATED" -eq 1 ] || [ "$PREVIOUS_APP_MOVED" -eq 1 ]; then
    if [ "$PREVIOUS_APP_MOVED" -eq 1 ]; then
      if [ -e "$TARGET_APP" ]; then
        rm -rf "$TARGET_APP" || true
      fi
      if [ ! -e "$TARGET_APP" ] && [ -e "$PREVIOUS_APP" ] && \
        mv "$PREVIOUS_APP" "$TARGET_APP"; then
        rollback_target_action="previous bundle restored"
      else
        rollback_target_action="previous bundle restore failed"
      fi
    else
      if [ -e "$TARGET_APP" ]; then
        rm -rf "$TARGET_APP" || true
      fi
      if [ -e "$TARGET_APP" ]; then
        rollback_target_action="new install removal failed"
      else
        rollback_target_action="new install removed"
      fi
    fi
  fi

  if [ "$ALDEN_PLIST_INSTALLED" -eq 1 ]; then
    if [ "$HAD_PREVIOUS_ALDEN_PLIST" -eq 1 ]; then
      if cp -p "$BACKUP_DIR/$ALDEN_LABEL.plist" "$ALDEN_PLIST"; then
        rollback_plist_action="previous plist restored"
      else
        rollback_plist_action="previous plist restore failed"
      fi
    else
      rm -f "$ALDEN_PLIST" || true
      if [ -e "$ALDEN_PLIST" ]; then
        rollback_plist_action="installed plist removal failed"
      else
        rollback_plist_action="installed plist removed"
      fi
    fi
  fi

  if [ -e "$BACKUP_DIR/$LEGACY_LABEL.disabled.plist" ]; then
    if mv "$BACKUP_DIR/$LEGACY_LABEL.disabled.plist" "$LEGACY_PLIST"; then
      rollback_legacy_plist_action="previous plist restored"
    else
      rollback_legacy_plist_action="previous plist restore failed"
    fi
  fi

  if [ "$HAD_JARVIS_PLIST" -eq 1 ]; then
    if [ -e "$BACKUP_DIR/$JARVIS_LABEL.disabled.plist" ]; then
      if cp -p "$BACKUP_DIR/$JARVIS_LABEL.disabled.plist" "$JARVIS_PLIST"; then
        rollback_jarvis_plist_action="previous plist restored"
      else
        rollback_jarvis_plist_action="previous plist restore failed"
      fi
    elif [ -f "$JARVIS_PLIST" ] && \
      /usr/bin/cmp -s "$BACKUP_DIR/$JARVIS_LABEL.plist" "$JARVIS_PLIST"; then
      rollback_jarvis_plist_action="previous plist remained in place"
    elif cp -p "$BACKUP_DIR/$JARVIS_LABEL.plist" "$JARVIS_PLIST"; then
      rollback_jarvis_plist_action="previous plist restored from backup"
    else
      rollback_jarvis_plist_action="previous plist restore failed"
    fi
  fi

  if [ "$ALDEN_LOADED" -eq 1 ]; then
    if [ -f "$ALDEN_PLIST" ] && \
      "$LAUNCHCTL" bootstrap "$DOMAIN" "$ALDEN_PLIST"; then
      rollback_alden_runtime_action="bootstrap restored"
    else
      rollback_alden_runtime_action="bootstrap restore failed"
    fi
  fi

  if [ "$LEGACY_LOADED" -eq 1 ]; then
    if [ -f "$LEGACY_PLIST" ] && \
      "$LAUNCHCTL" bootstrap "$DOMAIN" "$LEGACY_PLIST"; then
      rollback_legacy_runtime_action="bootstrap restored"
    else
      rollback_legacy_runtime_action="bootstrap restore failed"
    fi
  fi

  if [ "$JARVIS_LOADED" -eq 1 ]; then
    rollback_jarvis_service_readback="$BACKUP_DIR/$JARVIS_LABEL.rollback-service.txt"
    if "$LAUNCHCTL" print "$JARVIS_SERVICE" \
      >"$rollback_jarvis_service_readback" 2>&1; then
      rollback_jarvis_pid=$(launchd_pid_from_readback \
        "$rollback_jarvis_service_readback")
      rollback_jarvis_path=""
      if [ -n "$rollback_jarvis_pid" ]; then
        rollback_jarvis_path=$(process_executable_path \
          "$rollback_jarvis_pid" 2>/dev/null || true)
      fi
      if [ "$rollback_jarvis_path" = "$JARVIS_BIN" ]; then
        rollback_jarvis_runtime_action="verified service already loaded"
      else
        rollback_jarvis_runtime_action="loaded service ownership unknown; left untouched"
      fi
    else
      rollback_jarvis_pids_status=0
      rollback_jarvis_pids=$(list_jarvis_pids) || rollback_jarvis_pids_status=$?
      if [ "$rollback_jarvis_pids_status" -ne 0 ]; then
        rollback_jarvis_runtime_action="process enumeration unknown; bootstrap blocked"
      elif [ -n "$rollback_jarvis_pids" ]; then
        rollback_jarvis_runtime_action="live Jarvis process remains; bootstrap blocked"
      elif [ -f "$JARVIS_PLIST" ] && \
        "$LAUNCHCTL" bootstrap "$DOMAIN" "$JARVIS_PLIST"; then
        rollback_jarvis_runtime_action="bootstrap restored"
      else
        rollback_jarvis_runtime_action="bootstrap restore failed"
      fi
    fi
  fi

  if [ -e "$TARGET_APP" ]; then
    rollback_target_readback="present"
  else
    rollback_target_readback="absent"
  fi
  rollback_readback="$BACKUP_DIR/$ALDEN_LABEL.rollback.txt"
  if "$LAUNCHCTL" print "$DOMAIN" >"$rollback_readback" 2>&1; then
    if /usr/bin/grep -F "$ALDEN_LABEL" "$rollback_readback" >/dev/null 2>&1; then
      rollback_alden_loaded="yes"
    else
      rollback_alden_loaded="no"
    fi
    if /usr/bin/grep -F "$LEGACY_LABEL" "$rollback_readback" >/dev/null 2>&1; then
      rollback_legacy_loaded="yes"
    else
      rollback_legacy_loaded="no"
    fi
    if /usr/bin/grep -F "$JARVIS_LABEL" "$rollback_readback" >/dev/null 2>&1; then
      rollback_jarvis_loaded="yes"
    else
      rollback_jarvis_loaded="no"
    fi
  else
    rollback_alden_loaded="unknown"
    rollback_legacy_loaded="unknown"
    rollback_jarvis_loaded="unknown"
  fi

  printf 'install-alden-desktop: rollback target: %s (%s; readback %s)\n' \
    "$TARGET_APP" "$rollback_target_action" "$rollback_target_readback" >&2
  printf 'install-alden-desktop: rollback plist: %s (%s)\n' \
    "$ALDEN_PLIST" "$rollback_plist_action" >&2
  printf 'install-alden-desktop: rollback Alden runtime: %s\n' \
    "$rollback_alden_runtime_action" >&2
  printf 'install-alden-desktop: rollback legacy plist: %s (%s)\n' \
    "$LEGACY_PLIST" "$rollback_legacy_plist_action" >&2
  printf 'install-alden-desktop: rollback legacy runtime: %s\n' \
    "$rollback_legacy_runtime_action" >&2
  printf 'install-alden-desktop: rollback Jarvis plist: %s (%s)\n' \
    "$JARVIS_PLIST" "$rollback_jarvis_plist_action" >&2
  printf 'install-alden-desktop: rollback Jarvis runtime: %s\n' \
    "$rollback_jarvis_runtime_action" >&2
  printf 'install-alden-desktop: rollback Alden LaunchAgent loaded: %s\n' \
    "$rollback_alden_loaded" >&2
  printf 'install-alden-desktop: rollback legacy LaunchAgent loaded: %s\n' \
    "$rollback_legacy_loaded" >&2
  printf 'install-alden-desktop: rollback Jarvis LaunchAgent loaded: %s\n' \
    "$rollback_jarvis_loaded" >&2
  printf 'install-alden-desktop: rollback backups: %s\n' "$BACKUP_DIR" >&2
  if [ -n "$PREVIOUS_APP" ]; then
    printf 'install-alden-desktop: previous bundle backup path: %s\n' \
      "$PREVIOUS_APP" >&2
  fi
}

post_activation_failure() {
  failure_reason=$1
  perform_rollback "$failure_reason"
  exit 3
}

# Recovery artifact for a successful cutover. It stays separate from
# rollback.txt, which only exists when activation had to be rolled back.
record_installed_readback() {
  record_pid=$1
  record_file="$BACKUP_DIR/$ALDEN_LABEL.installed.txt"
  record_wait_log="$BACKUP_DIR/$ALDEN_LABEL.wait.txt"

  {
    printf 'service: %s\n' "$ALDEN_SERVICE"
    printf 'pid: %s\n' "$record_pid"
    printf 'plist: %s\n' "$ALDEN_PLIST"
    printf 'app: %s\n' "$TARGET_APP"
    printf 'backup: %s\n' "$BACKUP_DIR"
    printf 'recorded_utc: %s\n' "$(/bin/date -u +%Y-%m-%dT%H:%M:%SZ)"
  } >"$record_file" 2>/dev/null || return 1

  if [ -s "$record_wait_log" ]; then
    printf '\nwait-readback:\n' >>"$record_file" 2>/dev/null || return 1
    /bin/cat "$record_wait_log" >>"$record_file" 2>/dev/null || return 1
  fi

  if [ ! -s "$record_file" ]; then
    return 1
  fi
  return 0
}

"$DITTO" "$SOURCE_APP" "$STAGED_APP"
if [ ! -x "$STAGED_APP/Contents/MacOS/$APP_EXECUTABLE" ]; then
  echo "install-alden-desktop: staged app is incomplete" >&2
  exit 2
fi
if [ -x /usr/bin/xattr ]; then
  /usr/bin/xattr -dr com.apple.quarantine "$STAGED_APP" 2>/dev/null || true
fi

cp "$TEMPLATE" "$PLIST_STAGE"
"$PLISTBUDDY" -c "Set :ProgramArguments:0 $TARGET_BIN" "$PLIST_STAGE"
"$PLUTIL" -lint "$PLIST_STAGE" >/dev/null
chmod 600 "$PLIST_STAGE"

# Re-read the old Tauri service immediately before mutation. Its service pid,
# executable path, plist label, and plist program must all belong to the known
# Jarvis install. Unknown or independently changed state is left untouched.
if ! verify_jarvis_preflight; then
  exit 3
fi

# The saved states and plists above are authoritative backups. Only now may the
# old jobs be unloaded. Moving their plists prevents them returning at login.
# Anything from here on mutates runtime state, so a signal must run the same
# rollback even before the new bundle is activated.
RUNTIME_TOUCHED=1
if [ "$LEGACY_LOADED" -eq 1 ]; then
  if ! "$LAUNCHCTL" bootout "$LEGACY_SERVICE"; then
    post_activation_failure "legacy LaunchAgent bootout failed"
  fi
fi
if [ "$JARVIS_LOADED" -eq 1 ]; then
  if ! "$LAUNCHCTL" bootout "$JARVIS_SERVICE"; then
    post_activation_failure "Jarvis LaunchAgent bootout failed"
  fi
fi
if [ "$ALDEN_LOADED" -eq 1 ]; then
  if ! "$LAUNCHCTL" bootout "$ALDEN_SERVICE"; then
    post_activation_failure "previous Alden LaunchAgent bootout failed"
  fi
fi
if ! wait_for_absent "$LEGACY_SERVICE" \
  "$BACKUP_DIR/$LEGACY_LABEL.after-bootout.txt"; then
  post_activation_failure "legacy LaunchAgent is still loaded"
fi
if ! wait_for_absent "$JARVIS_SERVICE" \
  "$BACKUP_DIR/$JARVIS_LABEL.after-bootout.txt"; then
  post_activation_failure "Jarvis LaunchAgent is still loaded"
fi
if ! wait_for_absent "$ALDEN_SERVICE" \
  "$BACKUP_DIR/$ALDEN_LABEL.after-bootout.txt"; then
  post_activation_failure "Alden LaunchAgent is still loaded"
fi
if ! wait_for_jarvis_absent \
  "$BACKUP_DIR/$JARVIS_LABEL.process-after-bootout.txt"; then
  post_activation_failure "Jarvis process did not exit cleanly"
fi
if [ -f "$LEGACY_PLIST" ]; then
  if ! mv "$LEGACY_PLIST" "$BACKUP_DIR/$LEGACY_LABEL.disabled.plist"; then
    post_activation_failure "could not disable legacy LaunchAgent plist"
  fi
fi
if [ -f "$JARVIS_PLIST" ]; then
  if ! mv "$JARVIS_PLIST" "$BACKUP_DIR/$JARVIS_LABEL.disabled.plist"; then
    post_activation_failure "could not disable Jarvis LaunchAgent plist"
  fi
fi

# Copying completed inside /Applications. These renames expose only complete
# bundles at the stable target path.
if [ -e "$TARGET_APP" ]; then
  PREVIOUS_APP="$APPLICATIONS_DIR/.openkakao-alden.previous.$STAMP.$$.app"
  if ! mv "$TARGET_APP" "$PREVIOUS_APP"; then
    post_activation_failure "could not preserve previous Alden bundle"
  fi
  PREVIOUS_APP_MOVED=1
fi
if ! mv "$STAGED_APP" "$TARGET_APP"; then
  post_activation_failure "could not activate staged app"
fi
APP_ACTIVATED=1

if ! mv "$PLIST_STAGE" "$ALDEN_PLIST"; then
  post_activation_failure "could not install LaunchAgent plist"
fi
PLIST_STAGE=""
ALDEN_PLIST_INSTALLED=1
if ! "$LAUNCHCTL" bootstrap "$DOMAIN" "$ALDEN_PLIST"; then
  post_activation_failure "LaunchAgent bootstrap failed"
fi
ALDEN_BOOTSTRAPPED=1

KICKSTART_STATUS=0
KICKSTART_OUTPUT=$("$LAUNCHCTL" kickstart -kp "$ALDEN_SERVICE" 2>/dev/null) || \
  KICKSTART_STATUS=$?
LAUNCHD_PID=""
if [ "$KICKSTART_STATUS" -eq 0 ]; then
  case "$KICKSTART_OUTPUT" in
    ''|*[!0-9]*)
      ;;
    *)
      LAUNCHD_PID=$KICKSTART_OUTPUT
      ;;
  esac
else
  if ! "$LAUNCHCTL" kickstart -k "$ALDEN_SERVICE"; then
    post_activation_failure "LaunchAgent kickstart failed"
  fi
fi

if [ -z "$LAUNCHD_PID" ]; then
  if ! LAUNCHD_PID=$(wait_for_pid "$ALDEN_SERVICE" \
    "$BACKUP_DIR/$ALDEN_LABEL.wait.txt"); then
    post_activation_failure "LaunchAgent pid was never reported"
  fi
fi

LAUNCHD_START_MARKER=$("$PS" -p "$LAUNCHD_PID" -o lstart= 2>/dev/null |
  /usr/bin/sed -n '1p' || true)

# Fail closed before deleting the previous bundle. A surviving process may be
# executing an older bundle even when launchd itself reports only this service.
if ! check_duplicate_guard "$LAUNCHD_PID"; then
  post_activation_failure "duplicate instance guard failed"
fi

# Record the successful activation while the previous bundle is still present,
# so a missing recovery artifact can still be rolled back.
if ! record_installed_readback "$LAUNCHD_PID"; then
  post_activation_failure "LaunchAgent readback artifact was not written"
fi

# Re-enumerate immediately before the irreversible previous-bundle deletion so
# cleanup never relies on the earlier process snapshot.
if [ -n "$PREVIOUS_APP" ] && [ -e "$PREVIOUS_APP" ]; then
  launchd_recheck_marker=$("$PS" -p "$LAUNCHD_PID" -o lstart= 2>/dev/null |
    /usr/bin/sed -n '1p' || true)
  if [ -z "$LAUNCHD_START_MARKER" ] || [ -z "$launchd_recheck_marker" ] || \
    [ "$launchd_recheck_marker" != "$LAUNCHD_START_MARKER" ]; then
    post_activation_failure "launchd pid identity changed before deletion"
  fi
  if ! check_duplicate_guard "$LAUNCHD_PID"; then
    post_activation_failure "duplicate instance recheck failed"
  fi
  rm -rf "$PREVIOUS_APP"
fi

printf 'installed: %s\n' "$TARGET_APP"
printf 'LaunchAgent: %s\n' "$ALDEN_SERVICE"
printf 'backup: %s\n' "$BACKUP_DIR"
