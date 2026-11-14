#!/usr/bin/env bash
# Assemble a self-contained Vercel deploy bundle: web/ + the shared core modules.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/.vercel-bundle}"
rm -rf "$OUT" && mkdir -p "$OUT/api" "$OUT/public"
cp "$ROOT"/{agent,memory,llm,models,config,incidents_data,demo_agent}.py "$OUT/"
cp "$ROOT/web/api/index.py" "$OUT/api/index.py"
cp "$ROOT"/web/public/* "$OUT/public/"
cp "$ROOT/web/requirements.txt" "$ROOT/web/vercel.json" "$OUT/"
echo "$OUT"
