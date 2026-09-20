#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
npm test
npm run build
cargo test --manifest-path src-tauri/Cargo.toml
cargo check --manifest-path src-tauri/Cargo.toml
