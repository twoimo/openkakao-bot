#!/bin/sh
# Install the built Tauri app and cut the menu LaunchAgent over to Jarvis.
set -eu
umask 077

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
APP_NAME="OpenKakao Jarvis.app"
APP_EXECUTABLE="openkakao-jarvis-desktop"
BUNDLE_ROOT="$ROOT/desktop/src-tauri/target"
TEMPLATE="$ROOT/desktop/launchd/com.openkakao.jarvis.desktop.plist.example"

APPLICATIONS_DIR=${OPENKAKAO_APPLICATIONS_DIR:-/Applications}
LAUNCH_AGENTS_DIR=${OPENKAKAO_LAUNCH_AGENTS_DIR:-"$HOME/Library/LaunchAgents"}
BACKUP_BASE=${OPENKAKAO_JARVIS_BACKUP_DIR:-"$HOME/Library/Application Support/openkakao/install-backups/jarvis-desktop"}
LAUNCHCTL=${OPENKAKAO_LAUNCHCTL:-/bin/launchctl}
DITTO=${OPENKAKAO_DITTO:-/usr/bin/ditto}
PLISTBUDDY=${OPENKAKAO_PLISTBUDDY:-/usr/libexec/PlistBuddy}
PLUTIL=${OPENKAKAO_PLUTIL:-/usr/bin/plutil}
PS=${OPENKAKAO_PS:-/bin/ps}
PGREP=${OPENKAKAO_PGREP:-/usr/bin/pgrep}
LSOF=${OPENKAKAO_LSOF:-/usr/sbin/lsof}

LEGACY_LABEL="com.openkakao.auto-reply.menu"
JARVIS_LABEL="com.openkakao.jarvis.desktop"
UID_NOW=$(id -u)
DOMAIN="gui/$UID_NOW"
LEGACY_SERVICE="$DOMAIN/$LEGACY_LABEL"
JARVIS_SERVICE="$DOMAIN/$JARVIS_LABEL"
LEGACY_PLIST="$LAUNCH_AGENTS_DIR/$LEGACY_LABEL.plist"
JARVIS_PLIST="$LAUNCH_AGENTS_DIR/$JARVIS_LABEL.plist"
TARGET_APP="$APPLICATIONS_DIR/$APP_NAME"
TARGET_BIN="$TARGET_APP/Contents/MacOS/$APP_EXECUTABLE"

SOURCE_APP=${OPENKAKAO_JARVIS_APP_SOURCE:-}
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
  echo "install-jarvis-desktop: release Tauri bundle was not found" >&2
  echo "run scripts/build-jarvis-desktop.sh first" >&2
  exit 2
fi
if [ ! -x "$SOURCE_APP/Contents/MacOS/$APP_EXECUTABLE" ]; then
  echo "install-jarvis-desktop: bundle executable is missing: $SOURCE_APP" >&2
  exit 2
fi
if [ ! -f "$TEMPLATE" ]; then
  echo "install-jarvis-desktop: LaunchAgent template is missing: $TEMPLATE" >&2
  exit 2
fi
for tool in "$LAUNCHCTL" "$DITTO" "$PLISTBUDDY" "$PLUTIL" "$PS"; do
  if [ ! -x "$tool" ]; then
    echo "install-jarvis-desktop: required tool is not executable: $tool" >&2
    exit 2
  fi
done
if [ ! -d "$APPLICATIONS_DIR" ] || [ ! -w "$APPLICATIONS_DIR" ]; then
  echo "install-jarvis-desktop: cannot write to $APPLICATIONS_DIR" >&2
  exit 2
fi

mkdir -p "$LAUNCH_AGENTS_DIR" "$BACKUP_BASE"
STAMP=$(/bin/date +%Y%m%dT%H%M%S)
BACKUP_DIR="$BACKUP_BASE/$STAMP-$$"
mkdir "$BACKUP_DIR"

# Preserve launchd readback and both plists before stopping either job.
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
HAD_PREVIOUS_JARVIS_PLIST=0
if "$LAUNCHCTL" print "$JARVIS_SERVICE" \
  >"$BACKUP_DIR/$JARVIS_LABEL.launchctl.txt" 2>&1; then
  JARVIS_LOADED=1
fi
if [ -f "$JARVIS_PLIST" ]; then
  HAD_PREVIOUS_JARVIS_PLIST=1
  cp -p "$JARVIS_PLIST" "$BACKUP_DIR/$JARVIS_LABEL.plist"
else
  printf '%s\n' "not present: $JARVIS_PLIST" \
    >"$BACKUP_DIR/$JARVIS_LABEL.plist.missing"
fi

STAGING_DIR=$(mktemp -d "$APPLICATIONS_DIR/.openkakao-jarvis.install.XXXXXX")
STAGED_APP="$STAGING_DIR/$APP_NAME"
PLIST_STAGE=$(mktemp "$LAUNCH_AGENTS_DIR/.$JARVIS_LABEL.plist.XXXXXX")
PREVIOUS_APP=""
PREVIOUS_APP_MOVED=0
APP_ACTIVATED=0
JARVIS_PLIST_INSTALLED=0
JARVIS_BOOTSTRAPPED=0
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
    [ "$JARVIS_PLIST_INSTALLED" -eq 1 ] || [ "$JARVIS_BOOTSTRAPPED" -eq 1 ]; then
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

list_live_app_pids() {
  candidate_pids=""
  use_ps=0

  if [ -x "$PGREP" ]; then
    pgrep_status=0
    pgrep_output=$("$PGREP" -f "$APP_EXECUTABLE" 2>/dev/null) || pgrep_status=$?
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
      /usr/bin/awk -v executable="$APP_EXECUTABLE" '
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

  printf '%s\n' "$candidate_pids"
}

reported_app_path() {
  reported_pid=$1
  reported_path=""

  # Paths are diagnostic evidence only; duplicate identity is based on pid.
  # macOS ps may still show an exec-time path after a bundle has moved.
  if [ -x "$LSOF" ]; then
    reported_path=$("$LSOF" -p "$reported_pid" -a -d txt -Fn 2>/dev/null |
      /usr/bin/sed -n 's/^n//p' | /usr/bin/sed -n '1p' || true)
  fi
  if [ -z "$reported_path" ]; then
    reported_path=$("$PS" -p "$reported_pid" -o comm= 2>/dev/null |
      /usr/bin/sed -n '1p' || true)
  fi
  case "$reported_path" in
    /*)
      ;;
    *)
      reported_path="(unresolved)"
      ;;
  esac
  if [ -z "$reported_path" ]; then
    reported_path="(unresolved)"
  fi
  printf '%s\n' "$reported_path"
}

check_duplicate_guard() {
  guard_launchd_pid=$1
  live_app_pids=""
  guard_attempt=1

  # A pid reported by launchd is not proof that the app survived activation.
  # Require that pid to be a live app process before trusting the cutover, with
  # a short bounded wait for the exec window right after kickstart.
  while :; do
    if ! live_app_pids=$(list_live_app_pids); then
      echo "install-jarvis-desktop: cannot prove there is no duplicate instance; process enumeration is unknown" >&2
      return 2
    fi
    if printf '%s\n' "$live_app_pids" |
      /usr/bin/awk -v launchd_pid="$guard_launchd_pid" \
        '$0 == launchd_pid { found = 1 } END { exit found ? 0 : 1 }'; then
      break
    fi
    if [ "$guard_attempt" -ge 5 ]; then
      printf 'install-jarvis-desktop: launchd pid %s is not a live app process\n' \
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

  echo "install-jarvis-desktop: stray Jarvis process detected" >&2
  for stray_pid in $stray_pids; do
    stray_path=$(reported_app_path "$stray_pid")
    printf 'install-jarvis-desktop: stray pid %s reported path: %s\n' \
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
  rollback_jarvis_runtime_action="not previously loaded"
  rollback_legacy_plist_action="unchanged"
  rollback_legacy_runtime_action="not previously loaded"
  rollback_artifact="$BACKUP_DIR/$JARVIS_LABEL.installed.txt"

  printf 'install-jarvis-desktop: %s; rollback was attempted\n' \
    "$rollback_reason" >&2

  if [ -e "$rollback_artifact" ]; then
    if rm -f "$rollback_artifact" && [ ! -e "$rollback_artifact" ]; then
      echo "install-jarvis-desktop: rollback installed artifact: removed" >&2
    else
      echo "install-jarvis-desktop: rollback installed artifact: removal failed" >&2
    fi
  fi

  if [ "$JARVIS_BOOTSTRAPPED" -eq 1 ]; then
    if "$LAUNCHCTL" bootout "$JARVIS_SERVICE" >/dev/null 2>&1; then
      echo "install-jarvis-desktop: rollback active Jarvis LaunchAgent: bootout succeeded" >&2
    else
      echo "install-jarvis-desktop: rollback active Jarvis LaunchAgent: bootout failed" >&2
    fi
  fi

  if [ "$APP_ACTIVATED" -eq 1 ]; then
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

  if [ "$JARVIS_PLIST_INSTALLED" -eq 1 ]; then
    if [ "$HAD_PREVIOUS_JARVIS_PLIST" -eq 1 ]; then
      if cp -p "$BACKUP_DIR/$JARVIS_LABEL.plist" "$JARVIS_PLIST"; then
        rollback_plist_action="previous plist restored"
      else
        rollback_plist_action="previous plist restore failed"
      fi
    else
      rm -f "$JARVIS_PLIST" || true
      if [ -e "$JARVIS_PLIST" ]; then
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

  if [ "$JARVIS_LOADED" -eq 1 ]; then
    if [ -f "$JARVIS_PLIST" ] && \
      "$LAUNCHCTL" bootstrap "$DOMAIN" "$JARVIS_PLIST"; then
      rollback_jarvis_runtime_action="bootstrap restored"
    else
      rollback_jarvis_runtime_action="bootstrap restore failed"
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

  if [ -e "$TARGET_APP" ]; then
    rollback_target_readback="present"
  else
    rollback_target_readback="absent"
  fi
  rollback_readback="$BACKUP_DIR/$JARVIS_LABEL.rollback.txt"
  if "$LAUNCHCTL" print "$DOMAIN" >"$rollback_readback" 2>&1; then
    if /usr/bin/grep -F "$JARVIS_LABEL" "$rollback_readback" >/dev/null 2>&1; then
      rollback_jarvis_loaded="yes"
    else
      rollback_jarvis_loaded="no"
    fi
    if /usr/bin/grep -F "$LEGACY_LABEL" "$rollback_readback" >/dev/null 2>&1; then
      rollback_legacy_loaded="yes"
    else
      rollback_legacy_loaded="no"
    fi
  else
    rollback_jarvis_loaded="unknown"
    rollback_legacy_loaded="unknown"
  fi

  printf 'install-jarvis-desktop: rollback target: %s (%s; readback %s)\n' \
    "$TARGET_APP" "$rollback_target_action" "$rollback_target_readback" >&2
  printf 'install-jarvis-desktop: rollback plist: %s (%s)\n' \
    "$JARVIS_PLIST" "$rollback_plist_action" >&2
  printf 'install-jarvis-desktop: rollback Jarvis runtime: %s\n' \
    "$rollback_jarvis_runtime_action" >&2
  printf 'install-jarvis-desktop: rollback legacy plist: %s (%s)\n' \
    "$LEGACY_PLIST" "$rollback_legacy_plist_action" >&2
  printf 'install-jarvis-desktop: rollback legacy runtime: %s\n' \
    "$rollback_legacy_runtime_action" >&2
  printf 'install-jarvis-desktop: rollback Jarvis LaunchAgent loaded: %s\n' \
    "$rollback_jarvis_loaded" >&2
  printf 'install-jarvis-desktop: rollback legacy LaunchAgent loaded: %s\n' \
    "$rollback_legacy_loaded" >&2
  printf 'install-jarvis-desktop: rollback backups: %s\n' "$BACKUP_DIR" >&2
  if [ -n "$PREVIOUS_APP" ]; then
    printf 'install-jarvis-desktop: previous bundle backup path: %s\n' \
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
  record_file="$BACKUP_DIR/$JARVIS_LABEL.installed.txt"
  record_wait_log="$BACKUP_DIR/$JARVIS_LABEL.wait.txt"

  {
    printf 'service: %s\n' "$JARVIS_SERVICE"
    printf 'pid: %s\n' "$record_pid"
    printf 'plist: %s\n' "$JARVIS_PLIST"
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
  echo "install-jarvis-desktop: staged app is incomplete" >&2
  exit 2
fi
if [ -x /usr/bin/xattr ]; then
  /usr/bin/xattr -dr com.apple.quarantine "$STAGED_APP" 2>/dev/null || true
fi

cp "$TEMPLATE" "$PLIST_STAGE"
"$PLISTBUDDY" -c "Set :ProgramArguments:0 $TARGET_BIN" "$PLIST_STAGE"
"$PLUTIL" -lint "$PLIST_STAGE" >/dev/null
chmod 600 "$PLIST_STAGE"

# The legacy state and plist above are authoritative backups. Only now may the
# old jobs be unloaded. Moving the legacy plist prevents it returning at login.
# Anything from here on mutates runtime state, so a signal must run the same
# rollback even before the new bundle is activated.
RUNTIME_TOUCHED=1
if [ "$LEGACY_LOADED" -eq 1 ]; then
  "$LAUNCHCTL" bootout "$LEGACY_SERVICE"
fi
if [ "$JARVIS_LOADED" -eq 1 ]; then
  "$LAUNCHCTL" bootout "$JARVIS_SERVICE"
fi
if ! wait_for_absent "$LEGACY_SERVICE" \
  "$BACKUP_DIR/$LEGACY_LABEL.after-bootout.txt"; then
  echo "install-jarvis-desktop: legacy LaunchAgent is still loaded" >&2
  exit 3
fi
if ! wait_for_absent "$JARVIS_SERVICE" \
  "$BACKUP_DIR/$JARVIS_LABEL.after-bootout.txt"; then
  echo "install-jarvis-desktop: Jarvis LaunchAgent is still loaded" >&2
  exit 3
fi
if [ -f "$LEGACY_PLIST" ]; then
  mv "$LEGACY_PLIST" "$BACKUP_DIR/$LEGACY_LABEL.disabled.plist"
fi

# Copying completed inside /Applications. These renames expose only complete
# bundles at the stable target path.
if [ -e "$TARGET_APP" ]; then
  PREVIOUS_APP="$APPLICATIONS_DIR/.openkakao-jarvis.previous.$STAMP.$$.app"
  mv "$TARGET_APP" "$PREVIOUS_APP"
  PREVIOUS_APP_MOVED=1
fi
if ! mv "$STAGED_APP" "$TARGET_APP"; then
  if [ -n "$PREVIOUS_APP" ] && [ -e "$PREVIOUS_APP" ]; then
    mv "$PREVIOUS_APP" "$TARGET_APP"
  fi
  echo "install-jarvis-desktop: could not activate staged app" >&2
  exit 3
fi
APP_ACTIVATED=1

if ! mv "$PLIST_STAGE" "$JARVIS_PLIST"; then
  post_activation_failure "could not install LaunchAgent plist"
fi
PLIST_STAGE=""
JARVIS_PLIST_INSTALLED=1
if ! "$LAUNCHCTL" bootstrap "$DOMAIN" "$JARVIS_PLIST"; then
  post_activation_failure "LaunchAgent bootstrap failed"
fi
JARVIS_BOOTSTRAPPED=1

KICKSTART_STATUS=0
KICKSTART_OUTPUT=$("$LAUNCHCTL" kickstart -kp "$JARVIS_SERVICE" 2>/dev/null) || \
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
  if ! "$LAUNCHCTL" kickstart -k "$JARVIS_SERVICE"; then
    post_activation_failure "LaunchAgent kickstart failed"
  fi
fi

if [ -z "$LAUNCHD_PID" ]; then
  if ! LAUNCHD_PID=$(wait_for_pid "$JARVIS_SERVICE" \
    "$BACKUP_DIR/$JARVIS_LABEL.wait.txt"); then
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
printf 'LaunchAgent: %s\n' "$JARVIS_SERVICE"
printf 'backup: %s\n' "$BACKUP_DIR"
