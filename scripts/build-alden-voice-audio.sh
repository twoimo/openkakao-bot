#!/bin/sh
# Compile the fixed macOS voice-processing library into the existing voice payload.
set -eu
TASK_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TASK_OUTPUT="$TASK_ROOT/scripts/libalden_audio.dylib"
TASK_TEMP="$TASK_OUTPUT.build-$$"
trap 'rm -f "$TASK_TEMP"' EXIT HUP INT TERM
/usr/bin/xcrun swiftc -swift-version 5 -O -emit-library \
  -target arm64-apple-macosx13.0 -module-name AldenVoiceAudio \
  -Xlinker -install_name -Xlinker @rpath/libalden_audio.dylib \
  -framework AVFoundation \
  "$TASK_ROOT/voice/native/playback_envelope.swift" \
  "$TASK_ROOT/voice/native/alden_audio.swift" -o "$TASK_TEMP"
/usr/bin/codesign --force --sign - --identifier com.openkakao.alden.voice-audio "$TASK_TEMP"
chmod 644 "$TASK_TEMP"
mv "$TASK_TEMP" "$TASK_OUTPUT"
printf '%s\n' "$TASK_OUTPUT"
