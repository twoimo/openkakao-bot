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
if "$LAUNCHCTL" print "$JARVIS_SERVICE" \
  >"$BACKUP_DIR/$JARVIS_LABEL.launchctl.txt" 2>&1; then
  JARVIS_LOADED=1
fi
if [ -f "$JARVIS_PLIST" ]; then
  cp -p "$JARVIS_PLIST" "$BACKUP_DIR/$JARVIS_LABEL.plist"
else
  printf '%s\n' "not present: $JARVIS_PLIST" \
    >"$BACKUP_DIR/$JARVIS_LABEL.plist.missing"
fi

STAGING_DIR=$(mktemp -d "$APPLICATIONS_DIR/.openkakao-jarvis.install.XXXXXX")
STAGED_APP="$STAGING_DIR/$APP_NAME"
PLIST_STAGE=$(mktemp "$LAUNCH_AGENTS_DIR/.$JARVIS_LABEL.plist.XXXXXX")
PREVIOUS_APP=""

cleanup() {
  if [ -n "$PLIST_STAGE" ] && [ -f "$PLIST_STAGE" ]; then
    rm -f "$PLIST_STAGE"
  fi
  if [ -n "$STAGING_DIR" ] && [ -d "$STAGING_DIR" ]; then
    rm -rf "$STAGING_DIR"
  fi
}
trap cleanup EXIT HUP INT TERM

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
  if [ -x "$PGREP" ]; then
    "$PGREP" -f "$APP_EXECUTABLE" 2>/dev/null || true
    return 0
  fi

  "$PS" -axo pid=,command= 2>/dev/null |
    /usr/bin/awk -v executable="$APP_EXECUTABLE" '
      {
        pid = $1
        $1 = ""
        if ($0 ~ "(^|[[:space:]/])" executable "([[:space:]]|$)") {
          print pid
        }
      }
    ' || true
}

reported_app_path() {
  reported_pid=$1
  reported_path=""

  # Path reporting is diagnostic only: macOS ps may show the exec-time argv
  # path after that bundle has moved or been deleted. PID identity gates cleanup.
  if [ -x "$LSOF" ]; then
    reported_path=$("$LSOF" -p "$reported_pid" -a -d txt -Fn 2>/dev/null |
      /usr/bin/sed -n 's/^n//p' | /usr/bin/sed -n '1p' || true)
  fi
  if [ -z "$reported_path" ]; then
    reported_path=$("$PS" -p "$reported_pid" -o comm= 2>/dev/null |
      /usr/bin/sed -n '1p' || true)
  fi
  if [ -z "$reported_path" ]; then
    reported_path="(unresolved)"
  fi
  printf '%s\n' "$reported_path"
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
fi
if ! mv "$STAGED_APP" "$TARGET_APP"; then
  if [ -n "$PREVIOUS_APP" ] && [ -e "$PREVIOUS_APP" ]; then
    mv "$PREVIOUS_APP" "$TARGET_APP"
  fi
  echo "install-jarvis-desktop: could not activate staged app" >&2
  exit 3
fi

mv "$PLIST_STAGE" "$JARVIS_PLIST"
PLIST_STAGE=""
"$LAUNCHCTL" bootstrap "$DOMAIN" "$JARVIS_PLIST"
"$LAUNCHCTL" kickstart -k "$JARVIS_SERVICE"
if ! "$LAUNCHCTL" print "$JARVIS_SERVICE" \
  >"$BACKUP_DIR/$JARVIS_LABEL.installed.txt" 2>&1; then
  echo "install-jarvis-desktop: LaunchAgent readback failed" >&2
  exit 3
fi

# Fail closed before deleting the previous bundle. A surviving process may be
# executing an older bundle even when launchd itself reports only this service.
if ! LAUNCHD_PID=$(wait_for_pid "$JARVIS_SERVICE" \
  "$BACKUP_DIR/$JARVIS_LABEL.installed.txt"); then
  echo "install-jarvis-desktop: LaunchAgent pid was never reported; previous bundle is being kept" >&2
  exit 3
fi

STRAY_PIDS=$(list_live_app_pids |
  /usr/bin/awk -v launchd_pid="$LAUNCHD_PID" -v installer_pid="$$" '
    /^[0-9]+$/ && $0 != launchd_pid && $0 != installer_pid && !seen[$0]++ {
      print $0
    }
  ')
if [ -n "$STRAY_PIDS" ]; then
  echo "install-jarvis-desktop: stray Jarvis process detected; previous bundle is being kept" >&2
  for stray_pid in $STRAY_PIDS; do
    stray_path=$(reported_app_path "$stray_pid")
    printf 'install-jarvis-desktop: stray pid %s reported path: %s\n' \
      "$stray_pid" "$stray_path" >&2
  done
  exit 3
fi

if [ -n "$PREVIOUS_APP" ] && [ -e "$PREVIOUS_APP" ]; then
  rm -rf "$PREVIOUS_APP"
fi

printf 'installed: %s\n' "$TARGET_APP"
printf 'LaunchAgent: %s\n' "$JARVIS_SERVICE"
printf 'backup: %s\n' "$BACKUP_DIR"
