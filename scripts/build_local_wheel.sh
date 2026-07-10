#!/usr/bin/env bash
# Build the SAME single distribution PyPI ships: a platform wheel with the
# maturin-built native module merged into the pure hatchling wheel. Local
# installs then match PyPI (never the bare py3-none-any fallback + a
# separately-installed forgetforge_engine_native).
#
# Usage:  bash scripts/build_local_wheel.sh
# Output: dist-merged/cluxion_agentplugin_autoclearmemory-<ver>-<platform>.whl
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# preflight: fail fast with a clear message on a machine missing the build toolchain
for _t in uv maturin cargo; do command -v "$_t" >/dev/null 2>&1 || { echo "build_local_wheel: needs uv, maturin, and cargo (rust) on PATH (missing: $_t)" >&2; exit 1; }; done

echo "[1/4] clean previous build output (dist + dist-merged)"
rm -rf dist dist-merged
mkdir -p dist-merged

echo "[2/4] build pure py3-none-any wheel (hatchling)"
uv run --extra dev python -m build --wheel --outdir dist >/dev/null

echo "[3/4] build native abi3 wheel (maturin --release --strip)"
( cd rust/forgetforge_engine && maturin build --release --strip --out dist >/dev/null )

PURE="$(ls -t dist/cluxion_agentplugin_autoclearmemory-*-py3-none-any.whl | head -1)"
NATIVE="$(ls -t rust/forgetforge_engine/dist/forgetforge_engine_native-*.whl | head -1)"
echo "      pure   = $PURE"
echo "      native = $NATIVE"

echo "[4/4] merge native into pure wheel + retag (repack_native_wheel.py)"
uv run --no-project --with wheel python scripts/repack_native_wheel.py \
  --pure-wheel "$PURE" \
  --native-wheel "$NATIVE" \
  --out dist-merged/

echo
echo "MERGED WHEEL(S):"
ls -la dist-merged/
