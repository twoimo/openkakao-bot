#!/bin/sh
# Reproducibly package and offline-install Alden's pinned menubar CPython.
set -eu
umask 022

SOURCE_REPOSITORY='https://github.com/astral-sh/python-build-standalone'
SOURCE_TAG='20260924'
SOURCE_RELEASE_API='https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/20260924'
SOURCE_ASSET_ID='586642446'
SOURCE_ASSET='cpython-3.11.16+20260924-aarch64-apple-darwin-install_only_stripped.tar.gz'
SOURCE_SHA256='e1d745b07b6acc0641dbb3237d3c5953deeeed182141bab2242684076fd86547'
SOURCE_URL='https://github.com/astral-sh/python-build-standalone/releases/download/20260924/cpython-3.11.16%2B20260924-aarch64-apple-darwin-install_only_stripped.tar.gz'
SOURCE_CODE_URL='https://github.com/astral-sh/python-build-standalone/tree/20260924'
SOURCE_LICENSE_URL='https://github.com/astral-sh/python-build-standalone/blob/20260924/LICENSE'
SOURCE_PYTHON_LICENSES_URL='https://github.com/astral-sh/python-build-standalone/blob/20260924/python-licenses.rst'

PYTHON_VERSION='3.11.16'
RUNTIME_ASSET='Alden-menubar-cpython-3.11.16-macos-arm64.tar.gz'
RUNTIME_MANIFEST='alden-menubar-runtime-v1.json'
RUNTIME_INSTALLER='install-alden-menubar-runtime.sh'
RUNTIME_RELATIVE_PATH='Library/Application Support/openkakao/runtimes/menubar'
PROBE_SENTINEL='ALDEN_CPYTHON_3_11_16_ARM64_OK'

CLEANUP_ONE=''
CLEANUP_TWO=''
RUNTIME_PARENT=''

fail() {
  printf '%s\n' "alden-menubar-runtime: $*" >&2
  exit 2
}

safe_cleanup_dir() {
  path=${1:-}
  [ -n "$path" ] || return 0
  scratch_root=${TMPDIR:-/tmp}
  case "$path" in
    "$scratch_root"/alden-menubar-runtime.*)
      /bin/rm -rf -- "$path"
      ;;
    "$RUNTIME_PARENT"/.alden-menubar-runtime.*)
      [ -n "$RUNTIME_PARENT" ] && /bin/rm -rf -- "$path"
      ;;
  esac
}

cleanup() {
  safe_cleanup_dir "$CLEANUP_TWO"
  safe_cleanup_dir "$CLEANUP_ONE"
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

manifest_json() {
  /bin/cat <<EOF
{
  "schema": "alden-menubar-runtime-v1",
  "runtime": "menubar",
  "platform": "macos",
  "architecture": "arm64",
  "python": {
    "implementation": "CPython",
    "version": "$PYTHON_VERSION",
    "interpreter": "bin/python3.11"
  },
  "install_root": "~/Library/Application Support/openkakao/runtimes/menubar",
  "source": {
    "repository": "$SOURCE_REPOSITORY",
    "tag": "$SOURCE_TAG",
    "release_api_url": "$SOURCE_RELEASE_API",
    "github_release_immutable": true,
    "asset_id": $SOURCE_ASSET_ID,
    "asset": "$SOURCE_ASSET",
    "asset_sha256": "$SOURCE_SHA256",
    "asset_url": "$SOURCE_URL",
    "source_code_url": "$SOURCE_CODE_URL",
    "project_license": "MPL-2.0",
    "project_license_url": "$SOURCE_LICENSE_URL",
    "bundled_python_licenses_url": "$SOURCE_PYTHON_LICENSES_URL"
  },
  "release_asset": {
    "name": "$RUNTIME_ASSET",
    "sha256": "$SOURCE_SHA256",
    "byte_identity": "verbatim-upstream-asset"
  }
}
EOF
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

verify_archive_digest() {
  archive=$1
  require_regular_file "$archive" 'runtime archive'
  actual=$(sha256_file "$archive") || fail 'could not hash runtime archive'
  [ "$actual" = "$SOURCE_SHA256" ] || \
    fail "runtime archive SHA-256 mismatch: expected $SOURCE_SHA256, got $actual"
}

validate_archive_structure() {
  archive=$1
  scan_dir=$2
  names="$scan_dir/archive-names.txt"
  verbose="$scan_dir/archive-verbose.txt"

  /usr/bin/tar -tzf "$archive" >"$names" || fail 'could not list runtime archive'
  [ -s "$names" ] || fail 'runtime archive has no entries'

  while IFS= read -r member; do
    [ -n "$member" ] || fail 'runtime archive contains an empty path'
    case "$member" in
      /*|../*|*/../*|*/..|.|..)
        fail "unsafe runtime archive path: $member"
        ;;
      python/*)
        ;;
      *)
        fail "runtime archive entry escapes the python/ root: $member"
        ;;
    esac
  done <"$names"

  /usr/bin/tar -tvzf "$archive" >"$verbose" || fail 'could not inspect runtime archive entry types'
  while IFS= read -r entry; do
    [ -n "$entry" ] || continue
    type=$(printf '%s' "$entry" | /usr/bin/cut -c1)
    case "$type" in
      -|d)
        ;;
      l)
        case "$entry" in
          *' -> '*) target=${entry##* -> } ;;
          *) fail 'runtime archive has a malformed symlink entry' ;;
        esac
        case "$target" in
          ''|.|..|*/*)
            fail "runtime archive symlink target is not a local basename: $target"
            ;;
        esac
        ;;
      *)
        fail "runtime archive contains unsupported entry type: $type"
        ;;
    esac
  done <"$verbose"
}

validate_runtime_dir() {
  runtime=$1
  python_bin="$runtime/bin/python3.11"
  python_lib="$runtime/lib/libpython3.11.dylib"
  python_license="$runtime/lib/python3.11/LICENSE.txt"

  [ ! -L "$runtime" ] && [ -d "$runtime" ] || fail "runtime root is missing or a symlink: $runtime"
  [ ! -L "$runtime/bin" ] && [ -d "$runtime/bin" ] || fail 'runtime bin directory is missing or a symlink'
  [ ! -L "$runtime/lib" ] && [ -d "$runtime/lib" ] || fail 'runtime lib directory is missing or a symlink'
  require_regular_file "$python_bin" 'CPython interpreter'
  [ -x "$python_bin" ] || fail 'CPython interpreter is not executable'
  require_regular_file "$python_lib" 'CPython shared library'
  require_regular_file "$python_license" 'CPython license notice'

  python_file=$(/usr/bin/file -b "$python_bin") || fail 'could not inspect CPython Mach-O architecture'
  case "$python_file" in
    *'Mach-O 64-bit executable arm64'*) ;;
    *) fail "CPython interpreter is not a macOS arm64 Mach-O: $python_file" ;;
  esac

  library_file=$(/usr/bin/file -b "$python_lib") || fail 'could not inspect libpython Mach-O architecture'
  case "$library_file" in
    *'Mach-O 64-bit dynamically linked shared library arm64'*) ;;
    *) fail "libpython is not a macOS arm64 dylib: $library_file" ;;
  esac

  /usr/bin/otool -l "$python_bin" | /usr/bin/grep -F 'path @executable_path/../lib ' >/dev/null || \
    fail 'CPython interpreter is missing the relocatable @executable_path/../lib rpath'

  probe_code='import platform,sys; ok=sys.implementation.name=="cpython" and sys.version_info[:3]==(3,11,16) and platform.machine()=="arm64"; print("ALDEN_CPYTHON_3_11_16_ARM64_OK") if ok else None; raise SystemExit(0 if ok else 3)'
  probe_output=$(
    /usr/bin/env -i HOME="${HOME:-/var/empty}" PATH='/usr/bin:/bin' \
      "$python_bin" -I -S -c "$probe_code" 2>/dev/null
  ) || fail 'runtime probe did not execute as isolated CPython 3.11.16 arm64'
  [ "$probe_output" = "$PROBE_SENTINEL" ] || \
    fail 'runtime probe did not emit the exact CPython 3.11.16 arm64 sentinel'
}

make_scratch_dir() {
  scratch_root=${TMPDIR:-/tmp}
  case "$scratch_root" in
    /*) ;;
    *) fail 'TMPDIR must be absolute' ;;
  esac
  /usr/bin/mktemp -d "$scratch_root/alden-menubar-runtime.XXXXXX"
}

extract_and_validate() {
  archive=$1
  stage=$2
  scan="$stage/scan"
  extracted="$stage/extracted"
  /bin/mkdir "$scan" "$extracted"
  validate_archive_structure "$archive" "$scan"
  /usr/bin/tar -xzf "$archive" -C "$extracted" || fail 'could not extract runtime archive'
  validate_runtime_dir "$extracted/python"
}

check_parent_component() {
  path=$1
  if [ -L "$path" ]; then
    fail "runtime install path contains a symlink component: $path"
  fi
  if [ -e "$path" ] && [ ! -d "$path" ]; then
    fail "runtime install path component is not a directory: $path"
  fi
}

prepare_runtime_parent() {
  [ -n "${HOME:-}" ] || fail 'HOME is required for the fixed runtime path'
  case "$HOME" in
    /*) ;;
    *) fail 'HOME must be absolute' ;;
  esac
  case "$HOME" in
    */./*|*/../*|*/.|*/..)
      fail 'HOME contains dot path components'
      ;;
  esac

  home_component=$HOME
  while :; do
    check_parent_component "$home_component"
    [ "$home_component" = / ] && break
    home_component=${home_component%/*}
    [ -n "$home_component" ] || home_component=/
  done

  support="$HOME/Library/Application Support/openkakao"
  RUNTIME_PARENT="$support/runtimes"
  check_parent_component "$HOME"
  check_parent_component "$HOME/Library"
  check_parent_component "$HOME/Library/Application Support"
  check_parent_component "$support"
  check_parent_component "$RUNTIME_PARENT"

  /bin/mkdir -p "$RUNTIME_PARENT"

  check_parent_component "$HOME"
  check_parent_component "$HOME/Library"
  check_parent_component "$HOME/Library/Application Support"
  check_parent_component "$support"
  check_parent_component "$RUNTIME_PARENT"
}

package_runtime() {
  [ "$#" -eq 2 ] || fail 'usage: package SOURCE_ARCHIVE OUTPUT_DIR'
  source_archive=$1
  output_dir=$2
  verify_archive_digest "$source_archive"

  CLEANUP_ONE=$(make_scratch_dir) || fail 'could not create package verification directory'
  extract_and_validate "$source_archive" "$CLEANUP_ONE"

  if [ -e "$output_dir" ]; then
    [ ! -L "$output_dir" ] && [ -d "$output_dir" ] || fail "output path is not a safe directory: $output_dir"
  else
    /bin/mkdir -p "$output_dir"
  fi

  runtime_output="$output_dir/$RUNTIME_ASSET"
  manifest_output="$output_dir/$RUNTIME_MANIFEST"
  installer_output="$output_dir/$RUNTIME_INSTALLER"
  for output in "$runtime_output" "$manifest_output" "$installer_output"; do
    [ ! -e "$output" ] && [ ! -L "$output" ] || fail "refusing to replace existing release output: $output"
  done

  /bin/cp "$source_archive" "$runtime_output"
  /bin/chmod 0644 "$runtime_output"
  manifest_json >"$manifest_output"
  /bin/chmod 0644 "$manifest_output"
  require_regular_file "$0" 'runtime installer source'
  /bin/cp "$0" "$installer_output"
  /bin/chmod 0755 "$installer_output"

  packaged_sha=$(sha256_file "$runtime_output") || fail 'could not hash packaged runtime'
  [ "$packaged_sha" = "$SOURCE_SHA256" ] || fail 'packaged runtime is not byte-identical to the pinned upstream asset'
  printf '%s\n' "runtime_asset=$runtime_output"
  printf '%s\n' "runtime_manifest=$manifest_output"
  printf '%s\n' "runtime_installer=$installer_output"
}

install_runtime() {
  [ "$#" -eq 2 ] || fail 'usage: install RUNTIME_ARCHIVE MANIFEST_JSON'
  runtime_archive=$1
  supplied_manifest=$2

  [ "$(/usr/bin/uname -s)" = 'Darwin' ] || fail 'offline runtime install requires macOS'
  [ "$(/usr/bin/uname -m)" = 'arm64' ] || fail 'offline runtime install requires Apple Silicon arm64'
  verify_archive_digest "$runtime_archive"
  require_regular_file "$supplied_manifest" 'runtime manifest'

  CLEANUP_ONE=$(make_scratch_dir) || fail 'could not create runtime preflight directory'
  expected_manifest="$CLEANUP_ONE/expected-manifest.json"
  manifest_json >"$expected_manifest"
  /usr/bin/cmp -s "$expected_manifest" "$supplied_manifest" || \
    fail 'runtime manifest does not match the pinned provenance contract'

  extract_and_validate "$runtime_archive" "$CLEANUP_ONE"

  prepare_runtime_parent
  destination="$HOME/$RUNTIME_RELATIVE_PATH"
  if [ -e "$destination" ] || [ -L "$destination" ]; then
    fail "runtime destination already exists; refusing to overwrite it: $destination"
  fi

  CLEANUP_TWO=$(/usr/bin/mktemp -d "$RUNTIME_PARENT/.alden-menubar-runtime.XXXXXX") || \
    fail 'could not create atomic runtime staging directory'
  install_scan="$CLEANUP_TWO/scan"
  install_extract="$CLEANUP_TWO/extracted"
  /bin/mkdir "$install_scan" "$install_extract"
  validate_archive_structure "$runtime_archive" "$install_scan"
  /usr/bin/tar -xzf "$runtime_archive" -C "$install_extract" || fail 'could not extract runtime into install staging'
  validate_runtime_dir "$install_extract/python"

  /bin/mv "$install_extract/python" "$destination"
  [ ! -L "$destination/bin/python3.11" ] && [ -x "$destination/bin/python3.11" ] || \
    fail 'installed runtime interpreter is missing or unsafe after atomic move'
  printf '%s\n' "installed_runtime=$destination"
  printf '%s\n' "interpreter=$destination/bin/python3.11"
}

usage() {
  /bin/cat >&2 <<EOF
usage:
  $0 source-url
  $0 manifest
  $0 package SOURCE_ARCHIVE OUTPUT_DIR
  $0 install RUNTIME_ARCHIVE MANIFEST_JSON
EOF
  exit 2
}

command=${1:-}
case "$command" in
  source-url)
    [ "$#" -eq 1 ] || usage
    printf '%s\n' "$SOURCE_URL"
    ;;
  manifest)
    [ "$#" -eq 1 ] || usage
    manifest_json
    ;;
  package)
    shift
    package_runtime "$@"
    ;;
  install)
    shift
    install_runtime "$@"
    ;;
  *)
    usage
    ;;
esac
