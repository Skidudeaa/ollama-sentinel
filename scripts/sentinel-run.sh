#!/usr/bin/env bash
# sentinel-run.sh — launch ollama-sentinel with a one-screen config summary
#
# Usage: ./scripts/sentinel-run.sh [ollama-sentinel options]
#   All arguments are forwarded to `ollama-sentinel run`.
#
# Model roles (pass with -m when using `ollama-sentinel review`):
#   default   general code review           (configured in YAML)
#   security  security-focused review       (configured in YAML)
#   triage    diagnose tool output/logs     (ollama-sentinel triage)

set -euo pipefail

OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
CONFIG="${SENTINEL_CONFIG:-ollama-sentinel.yaml}"

# --- 1. Verify Ollama is running
if ! curl -sf "$OLLAMA_HOST/api/tags" > /dev/null 2>&1; then
  echo "Error: Ollama not responding at $OLLAMA_HOST"
  echo "Start it with:  ollama serve"
  exit 1
fi

# --- 2. Verify config exists
if [ ! -f "$CONFIG" ]; then
  echo "Error: $CONFIG not found."
  echo "Run first:  ./scripts/sentinel-init.sh [WATCH_DIR]"
  exit 1
fi

# --- 3. Print config summary + cloud warning
python3 - "$CONFIG" <<'PYEOF'
import sys, pathlib

try:
    import yaml
except ImportError:
    print("(install pyyaml to see config summary)")
    sys.exit(0)

config = yaml.safe_load(pathlib.Path(sys.argv[1]).read_text())

reviewer = (config.get("ollama", {})
                  .get("models", {})
                  .get("default", {})
                  .get("name", "unknown"))
embedder = (config.get("embedding", {})
                  .get("models", {})
                  .get("hot", "unknown"))
watch_dir = config.get("watch", {}).get("directory", ".")
output_dir = config.get("output", {}).get("directory", ".ollama_reviews")

print("=== ollama-sentinel ===")
print(f"  Watch dir  : {watch_dir}")
print(f"  Reviews    : {output_dir}/")
print(f"  Reviewer   : {reviewer}", end="")
if ":cloud" in reviewer:
    print("  ⚠  cloud-routed (code leaves your machine)", end="")
print()
print(f"  Embedder   : {embedder}")
print()
print("  Quick reference:")
print("    ollama-sentinel review <file>            # one-off review")
print("    ollama-sentinel review <file> -m security  # security role")
print("    ollama-sentinel triage < pytest.log      # diagnose tool output")
print("    ollama-sentinel report                   # recurring violations")
print("    ollama-sentinel dashboard                # live TUI")
print()
PYEOF

# --- 4. Launch
exec ollama-sentinel run "$@"
