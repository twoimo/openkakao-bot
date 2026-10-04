#!/bin/sh
# Offline provisioner for the dedicated Alden Browser-Use runtime.
set -eu
umask 022

SOURCE_REPOSITORY='https://github.com/astral-sh/python-build-standalone'
SOURCE_TAG='20260924'
SOURCE_ASSET='cpython-3.11.16+20260924-aarch64-apple-darwin-install_only_stripped.tar.gz'
SOURCE_SHA256='e1d745b07b6acc0641dbb3237d3c5953deeeed182141bab2242684076fd86547'
SOURCE_URL='https://github.com/astral-sh/python-build-standalone/releases/download/20260924/cpython-3.11.16%2B20260924-aarch64-apple-darwin-install_only_stripped.tar.gz'

PYTHON_VERSION='3.11.16'
REQUIREMENTS_SHA256='f6db6bffa4c481a946aa58729b14b44ae6ca5ec5066ac526e1712cd3055250ca'
BROWSER_USE_VERSION='0.13.10'
PLAYWRIGHT_VERSION='1.63.0'
CHROMIUM_REVISION='1243'
FFMPEG_REVISION='1011'
RUNTIME_RELATIVE_PATH='Library/Application Support/openkakao/runtimes/browser'
PROBE_SENTINEL='ALDEN_BROWSER_RUNTIME_OK'

CLEANUP_ONE=''
RUNTIME_PARENT=''

fail() {
  printf '%s\n' "alden-browser-runtime: $*" >&2
  exit 2
}

sha256_file() {
  /usr/bin/shasum -a 256 "$1" | /usr/bin/awk '{print $1}'
}

require_regular_file() {
  path=$1
  label=$2
  [ ! -L "$path" ] || fail "$label is a symlink: $path"
  [ -f "$path" ] && [ -s "$path" ] && [ -r "$path" ] || \
    fail "$label is missing, empty, or unreadable: $path"
}

require_file() {
  path=$1
  label=$2
  [ ! -L "$path" ] || fail "$label is a symlink: $path"
  [ -f "$path" ] && [ -r "$path" ] || fail "$label is missing or unreadable: $path"
}

require_safe_directory() {
  path=$1
  label=$2
  [ ! -L "$path" ] || fail "$label is a symlink: $path"
  [ -d "$path" ] && [ -x "$path" ] || fail "$label is missing or unsafe: $path"
}

verify_digest() {
  path=$1
  expected=$2
  label=$3
  require_regular_file "$path" "$label"
  actual=$(sha256_file "$path") || fail "could not hash $label"
  [ "$actual" = "$expected" ] || \
    fail "$label SHA-256 mismatch: expected $expected, got $actual"
}

verify_requirements() {
  requirements=$1
  verify_digest "$requirements" "$REQUIREMENTS_SHA256" 'frozen browser requirements'
  /usr/bin/grep -F 'browser-use==0.13.10 ' "$requirements" >/dev/null || \
    fail 'frozen browser requirements are missing browser-use==0.13.10'
  /usr/bin/grep -F 'playwright==1.63.0 ' "$requirements" >/dev/null || \
    fail 'frozen browser requirements are missing playwright==1.63.0'
}

validate_wheelhouse() {
  wheelhouse=$1
  require_safe_directory "$wheelhouse" 'browser wheelhouse'
  count=0
  for artifact in "$wheelhouse"/*; do
    [ -e "$artifact" ] || continue
    case "$artifact" in
      *.whl) ;;
      *) fail "wheelhouse contains a non-wheel artifact: $artifact" ;;
    esac
    require_regular_file "$artifact" 'wheelhouse artifact'
    count=$((count + 1))
  done
  [ "$count" -gt 0 ] || fail 'browser wheelhouse is empty'
}

validate_browser_payload() {
  browsers=$1
  require_safe_directory "$browsers" 'Playwright browser payload'

  chromium="$browsers/chromium-$CHROMIUM_REVISION"
  headless="$browsers/chromium_headless_shell-$CHROMIUM_REVISION"
  ffmpeg="$browsers/ffmpeg-$FFMPEG_REVISION"
  require_safe_directory "$chromium" 'Chromium revision directory'
  require_safe_directory "$headless" 'Chromium headless-shell revision directory'
  require_safe_directory "$ffmpeg" 'Playwright ffmpeg revision directory'

  require_file "$chromium/INSTALLATION_COMPLETE" 'Chromium installation marker'
  require_file "$headless/INSTALLATION_COMPLETE" 'Chromium headless-shell installation marker'
  require_file "$ffmpeg/INSTALLATION_COMPLETE" 'Playwright ffmpeg installation marker'

  chromium_bin="$chromium/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
  headless_bin="$headless/chrome-headless-shell-mac-arm64/chrome-headless-shell"
  ffmpeg_bin="$ffmpeg/ffmpeg-mac"
  require_regular_file "$chromium_bin" 'Chromium executable'
  require_regular_file "$headless_bin" 'Chromium headless-shell executable'
  require_regular_file "$ffmpeg_bin" 'Playwright ffmpeg executable'
  [ -x "$chromium_bin" ] || fail 'Chromium executable is not executable'
  [ -x "$headless_bin" ] || fail 'Chromium headless-shell executable is not executable'
  [ -x "$ffmpeg_bin" ] || fail 'Playwright ffmpeg executable is not executable'
}

validate_archive_structure() {
  archive=$1
  scan_dir=$2
  names="$scan_dir/archive-names.txt"
  verbose="$scan_dir/archive-verbose.txt"
  /usr/bin/tar -tzf "$archive" >"$names" || fail 'could not list CPython archive'
  [ -s "$names" ] || fail 'CPython archive has no entries'
  while IFS= read -r member; do
    case "$member" in
      ''|/*|../*|*/../*|*/..|.|..) fail "unsafe CPython archive path: $member" ;;
      python/*) ;;
      *) fail "CPython archive entry escapes the python/ root: $member" ;;
    esac
  done <"$names"
  /usr/bin/tar -tvzf "$archive" >"$verbose" || fail 'could not inspect CPython archive entry types'
  while IFS= read -r entry; do
    [ -n "$entry" ] || continue
    type=$(printf '%s' "$entry" | /usr/bin/cut -c1)
    case "$type" in
      -|d) ;;
      l)
        case "$entry" in
          *' -> '*) target=${entry##* -> } ;;
          *) fail 'CPython archive has a malformed symlink entry' ;;
        esac
        case "$target" in
          ''|.|..|*/*) fail "CPython archive symlink target is not a local basename: $target" ;;
        esac
        ;;
      *) fail "CPython archive contains unsupported entry type: $type" ;;
    esac
  done <"$verbose"
}

validate_python_runtime() {
  runtime=$1
  python_bin="$runtime/bin/python3.11"
  python_lib="$runtime/lib/libpython3.11.dylib"
  python_license="$runtime/lib/python3.11/LICENSE.txt"
  require_safe_directory "$runtime" 'browser runtime root'
  require_safe_directory "$runtime/bin" 'browser runtime bin directory'
  require_safe_directory "$runtime/lib" 'browser runtime lib directory'
  require_regular_file "$python_bin" 'browser CPython interpreter'
  require_regular_file "$python_lib" 'browser CPython shared library'
  require_regular_file "$python_license" 'browser CPython license notice'
  [ -x "$python_bin" ] || fail 'browser CPython interpreter is not executable'

  python_file=$(/usr/bin/file -b "$python_bin") || fail 'could not inspect browser CPython architecture'
  case "$python_file" in
    *'Mach-O 64-bit executable arm64'*) ;;
    *) fail "browser CPython interpreter is not a macOS arm64 Mach-O: $python_file" ;;
  esac
  library_file=$(/usr/bin/file -b "$python_lib") || fail 'could not inspect browser libpython architecture'
  case "$library_file" in
    *'Mach-O 64-bit dynamically linked shared library arm64'*) ;;
    *) fail "browser libpython is not a macOS arm64 dylib: $library_file" ;;
  esac
  /usr/bin/otool -l "$python_bin" | /usr/bin/grep -F 'path @executable_path/../lib ' >/dev/null || \
    fail 'browser CPython interpreter is missing the relocatable rpath'

  probe='import platform,sys; ok=sys.implementation.name=="cpython" and sys.version_info[:3]==(3,11,16) and platform.machine()=="arm64"; print("ALDEN_BROWSER_RUNTIME_OK") if ok else None; raise SystemExit(0 if ok else 3)'
  output=$(/usr/bin/env -i HOME="${HOME:-/var/empty}" PATH='/usr/bin:/bin' \
    "$python_bin" -I -S -c "$probe" 2>/dev/null) || fail 'browser CPython isolated probe failed'
  [ "$output" = "$PROBE_SENTINEL" ] || fail 'browser CPython isolated probe returned the wrong sentinel'
}

check_parent_component() {
  path=$1
  [ ! -L "$path" ] || fail "runtime install path contains a symlink component: $path"
  if [ -e "$path" ] && [ ! -d "$path" ]; then
    fail "runtime install path component is not a directory: $path"
  fi
}

prepare_runtime_parent() {
  [ -n "${HOME:-}" ] || fail 'HOME is required for the fixed browser runtime path'
  case "$HOME" in /*) ;; *) fail 'HOME must be absolute' ;; esac
  case "$HOME" in */./*|*/../*|*/.|*/..) fail 'HOME contains dot path components' ;; esac
  component=$HOME
  while :; do
    check_parent_component "$component"
    [ "$component" = / ] && break
    component=${component%/*}
    [ -n "$component" ] || component=/
  done
  support="$HOME/Library/Application Support/openkakao"
  RUNTIME_PARENT="$support/runtimes"
  check_parent_component "$HOME/Library"
  check_parent_component "$HOME/Library/Application Support"
  check_parent_component "$support"
  check_parent_component "$RUNTIME_PARENT"
  /bin/mkdir -p "$RUNTIME_PARENT"
  check_parent_component "$RUNTIME_PARENT"
}

cleanup() {
  if [ -n "$CLEANUP_ONE" ]; then
    case "$CLEANUP_ONE" in
      "$RUNTIME_PARENT"/.alden-browser-runtime.*) /bin/rm -rf -- "$CLEANUP_ONE" ;;
    esac
  fi
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

manifest_json() {
  /bin/cat <<EOF
{
  "schema": "alden-browser-runtime-v1",
  "runtime": "browser",
  "install_root": "~/Library/Application Support/openkakao/runtimes/browser",
  "python": {"version": "$PYTHON_VERSION", "asset": "$SOURCE_ASSET", "sha256": "$SOURCE_SHA256"},
  "requirements": {"sha256": "$REQUIREMENTS_SHA256", "browser-use": "$BROWSER_USE_VERSION", "playwright": "$PLAYWRIGHT_VERSION"},
  "playwright": {"chromium_revision": "$CHROMIUM_REVISION", "ffmpeg_revision": "$FFMPEG_REVISION", "path": "ms-playwright"}
}
EOF
}

preflight_inputs() {
  [ "$#" -eq 3 ] || fail 'usage: preflight REQUIREMENTS WHEELHOUSE PLAYWRIGHT_PAYLOAD'
  verify_requirements "$1"
  validate_wheelhouse "$2"
  validate_browser_payload "$3"
  printf '%s\n' 'preflight=ok'
}

install_runtime() {
  [ "$#" -eq 4 ] || fail 'usage: install CPYTHON_ARCHIVE REQUIREMENTS WHEELHOUSE PLAYWRIGHT_PAYLOAD'
  archive=$1
  requirements=$2
  wheelhouse=$3
  browsers=$4
  [ "$(/usr/bin/uname -s)" = 'Darwin' ] || fail 'browser runtime install requires macOS'
  [ "$(/usr/bin/uname -m)" = 'arm64' ] || fail 'browser runtime install requires Apple Silicon arm64'

  verify_digest "$archive" "$SOURCE_SHA256" 'CPython archive'
  verify_requirements "$requirements"
  validate_wheelhouse "$wheelhouse"
  validate_browser_payload "$browsers"
  prepare_runtime_parent
  destination="$HOME/$RUNTIME_RELATIVE_PATH"
  if [ -e "$destination" ] || [ -L "$destination" ]; then
    fail "browser runtime destination already exists; refusing to overwrite it: $destination"
  fi

  CLEANUP_ONE=$(/usr/bin/mktemp -d "$RUNTIME_PARENT/.alden-browser-runtime.XXXXXX") || \
    fail 'could not create browser runtime staging directory'
  scan="$CLEANUP_ONE/scan"
  extracted="$CLEANUP_ONE/extracted"
  /bin/mkdir "$scan" "$extracted"
  validate_archive_structure "$archive" "$scan"
  /usr/bin/tar -xzf "$archive" -C "$extracted" || fail 'could not extract CPython into browser runtime staging'
  runtime="$extracted/python"
  validate_python_runtime "$runtime"

  python_bin="$runtime/bin/python3.11"
  /usr/bin/env -i HOME="$HOME" PATH='/usr/bin:/bin' \
    "$python_bin" -I -m pip install --disable-pip-version-check --no-input \
    --no-index --find-links "$wheelhouse" --require-hashes -r "$requirements" >/dev/null || \
    fail 'offline browser dependency installation failed'

  /bin/mkdir "$runtime/ms-playwright"
  /bin/cp -R "$browsers/chromium-$CHROMIUM_REVISION" "$runtime/ms-playwright/"
  /bin/cp -R "$browsers/chromium_headless_shell-$CHROMIUM_REVISION" "$runtime/ms-playwright/"
  /bin/cp -R "$browsers/ffmpeg-$FFMPEG_REVISION" "$runtime/ms-playwright/"
  validate_browser_payload "$runtime/ms-playwright"

  readiness='import importlib.metadata as m, pathlib; assert m.version("browser-use")=="0.13.10"; assert m.version("playwright")=="1.63.0"; from playwright.sync_api import sync_playwright; p=sync_playwright().start(); e=pathlib.Path(p.chromium.executable_path); p.stop(); assert e.exists(); print("ALDEN_BROWSER_RUNTIME_OK")'
  ready=$(/usr/bin/env -i HOME="$HOME" PATH='/usr/bin:/bin' \
    PLAYWRIGHT_BROWSERS_PATH="$runtime/ms-playwright" ANONYMIZED_TELEMETRY='false' \
    "$python_bin" -I -c "$readiness" 2>/dev/null) || fail 'browser dependency readiness probe failed'
  [ "$ready" = "$PROBE_SENTINEL" ] || fail 'browser dependency readiness probe returned the wrong sentinel'

  /bin/mv "$runtime" "$destination"
  require_regular_file "$destination/bin/python3.11" 'installed browser interpreter'
  require_safe_directory "$destination/ms-playwright" 'installed browser payload'
  printf '%s\n' "installed_runtime=$destination"
  printf '%s\n' "interpreter=$destination/bin/python3.11"
  printf '%s\n' "playwright_browsers=$destination/ms-playwright"
}

usage() {
  /bin/cat >&2 <<EOF
usage:
  $0 source-url
  $0 manifest
  $0 preflight REQUIREMENTS WHEELHOUSE PLAYWRIGHT_PAYLOAD
  $0 install CPYTHON_ARCHIVE REQUIREMENTS WHEELHOUSE PLAYWRIGHT_PAYLOAD
EOF
  exit 2
}

command=${1:-}
case "$command" in
  source-url) [ "$#" -eq 1 ] || usage; printf '%s\n' "$SOURCE_URL" ;;
  manifest) [ "$#" -eq 1 ] || usage; manifest_json ;;
  preflight) shift; preflight_inputs "$@" ;;
  install) shift; install_runtime "$@" ;;
  *) usage ;;
esac
