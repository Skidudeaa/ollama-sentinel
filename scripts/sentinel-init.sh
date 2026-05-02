#!/usr/bin/env bash
# sentinel-init.sh — interactive setup for ollama-sentinel
# Usage: ./scripts/sentinel-init.sh [WATCH_DIR]

set -euo pipefail

WATCH_DIR="${1:-$(pwd)}"
OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"

echo "=== ollama-sentinel init ==="
echo ""

# --- 1. Verify Ollama is running
if ! curl -sf "$OLLAMA_HOST/api/tags" > /dev/null 2>&1; then
  echo "Error: Ollama not responding at $OLLAMA_HOST"
  echo "Start it with:  ollama serve"
  exit 1
fi

# --- 2. Build model list and let the user pick
REVIEWER=$(python3 - "$OLLAMA_HOST" <<'PYEOF'
import sys, json, urllib.request

host = sys.argv[1]
with urllib.request.urlopen(f"{host}/api/tags", timeout=5) as r:
    models = json.loads(r.read())["models"]

# Annotations: preferred local models + cloud flags
PREFERRED_LOCAL = {
    "gemma4:26b", "gemma4:12b", "gemma4:4b",
    "gemma3:12b", "gemma3:4b",
    "devstral:latest", "devstral:24b",
}

# Skip embedding models — they're not reviewers
def is_embedder(name):
    return "embed" in name.lower()

entries = []
for m in models:
    name = m["name"]
    if is_embedder(name):
        continue
    is_cloud = ":cloud" in name
    is_preferred = name in PREFERRED_LOCAL
    tag = ""
    if is_cloud:
        tag = "  [cloud — code leaves your machine]"
    elif is_preferred:
        tag = "  [recommended local]"
    entries.append((name, tag))

if not entries:
    print("NO_MODELS", file=sys.stderr)
    sys.exit(1)

sys.stderr.write("Available reviewer models:\n\n")
for i, (name, tag) in enumerate(entries, 1):
    sys.stderr.write(f"  {i}) {name}{tag}\n")
sys.stderr.write("\n")

with open("/dev/tty") as tty:
    while True:
        sys.stderr.write("Pick a reviewer model (number): ")
        sys.stderr.flush()
        choice = tty.readline().strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(entries):
                print(entries[idx][0])
                break
        except ValueError:
            pass
        sys.stderr.write(f"  Enter a number between 1 and {len(entries)}\n")
PYEOF
)

echo ""
echo "Reviewer  : $REVIEWER"

# --- 3. Verify embedder
EMBEDDER="qwen3-embedding:4b"
AVAILABLE=$(curl -sf "$OLLAMA_HOST/api/tags" \
  | python3 -c "import sys,json; print('\n'.join(m['name'] for m in json.load(sys.stdin)['models']))")

if ! echo "$AVAILABLE" | grep -qx "$EMBEDDER"; then
  echo "Embedder not found — pulling $EMBEDDER (~2.5 GB) ..."
  ollama pull "$EMBEDDER"
fi
echo "Embedder  : $EMBEDDER (semantic recall)"
echo "Watch dir : $WATCH_DIR"
echo ""

# --- 4. Run ollama-sentinel init
ollama-sentinel init "$WATCH_DIR"

# --- 5. Patch YAML with chosen reviewer (default + triage roles)
python3 - "$REVIEWER" <<'PYEOF'
import sys, re, pathlib

reviewer = sys.argv[1]
p = pathlib.Path("ollama-sentinel.yaml")
text = p.read_text()
text = re.sub(r'(?m)(^\s+name:\s*)gemma3:4b', rf'\g<1>{reviewer}', text)
p.write_text(text)
print(f"Config written: ollama-sentinel.yaml")
PYEOF
