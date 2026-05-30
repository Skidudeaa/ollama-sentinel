#!/usr/bin/env bash
#
# setup-sentinel-project.sh — one-stop setup for using ollama-sentinel in a
# project. Run it from inside the project you want reviewed:
#
#     ~/jan25/ollama-sentinel/scripts/setup-sentinel-project.sh
#
# It verifies Ollama + the CLI, makes sure the models are pulled, writes a
# valid ollama-sentinel.yaml pointed at your local models, pins the Python
# version so the `ollama-sentinel` command resolves here, and ignores the
# review output. Idempotent: safe to re-run.
#
# Usage:
#   setup-sentinel-project.sh [-m MODEL] [-y] [--run]
#     -m MODEL   reviewer model (default: qwen3.6:35b)
#     -y         auto-pull missing models without asking
#     --run      start `ollama-sentinel run` after setup
#
set -euo pipefail

MODEL="qwen3-coder:30b"   # returns schema-valid JSON -> grounded path + verbatim validation
EMBED_MODEL="qwen3-embedding:4b"
AUTO_YES=0
START_RUN=0
OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -m|--model) MODEL="$2"; shift 2 ;;
    -y|--yes)   AUTO_YES=1; shift ;;
    --run)      START_RUN=1; shift ;;
    -h|--help)  grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# --- 1. Resolve the ollama-sentinel CLI (installed under pyenv 3.12.0) -------
say "Locating ollama-sentinel..."
# Prefer pyenv's real versioned path over the shim: the shim's sibling
# `python` resolves to whatever pyenv version is active in the cwd (often the
# global, which lacks the package), which would break config generation.
SENTINEL_BIN="$(pyenv which ollama-sentinel 2>/dev/null || true)"
if [[ -z "$SENTINEL_BIN" || ! -x "$SENTINEL_BIN" ]]; then
  SENTINEL_BIN="$(command -v ollama-sentinel 2>/dev/null || true)"
fi
if [[ -z "$SENTINEL_BIN" || ! -x "$SENTINEL_BIN" ]]; then
  for cand in "$HOME"/.pyenv/versions/3.12.*/bin/ollama-sentinel; do
    [[ -x "$cand" ]] && SENTINEL_BIN="$cand" && break
  done
fi
[[ -x "$SENTINEL_BIN" ]] || die "ollama-sentinel not found. Install it first: pip install -e ~/jan25/ollama-sentinel"
PYBIN="$(dirname "$SENTINEL_BIN")/python"
# Exact pyenv version name for `pyenv local` — must match an installed dir
# (e.g. 3.12.0, not 3.12). Derive it from the .../versions/<NAME>/bin path;
# fall back to the full micro version if the CLI lives outside pyenv.
PYVER="$(printf '%s' "$SENTINEL_BIN" | sed -nE 's#.*/versions/([^/]+)/bin/.*#\1#p')"
[[ -n "$PYVER" ]] || PYVER="$("$PYBIN" -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])' 2>/dev/null || echo "")"
ok "CLI: $SENTINEL_BIN (python $PYVER)"

# --- 2. Verify Ollama is up -------------------------------------------------
say "Checking Ollama at $OLLAMA_HOST..."
curl -sf "$OLLAMA_HOST/api/version" >/dev/null 2>&1 \
  || die "Ollama is not responding at $OLLAMA_HOST. Start it (run: ollama serve) and re-run."
ok "Ollama is running"

# --- 3. Ensure the models are pulled ---------------------------------------
have_model() {
  curl -sf "$OLLAMA_HOST/api/tags" \
    | "$PYBIN" -c "import sys,json; ns={m['name'] for m in json.load(sys.stdin).get('models',[])}; sys.exit(0 if '$1' in ns else 1)" \
    2>/dev/null
}
ensure_model() {
  local m="$1" kind="$2"
  if have_model "$m"; then ok "$kind model present: $m"; return; fi
  warn "$kind model '$m' is not pulled."
  if [[ "$AUTO_YES" -eq 1 ]]; then
    say "Pulling $m (this can be large)..."; ollama pull "$m"
  else
    read -r -p "Pull $m now? [y/N] " ans
    [[ "$ans" =~ ^[Yy] ]] && { say "Pulling $m..."; ollama pull "$m"; } \
      || die "Cannot proceed without $m. Pull it or pass -m with a model you have."
  fi
  ok "$kind model ready: $m"
}
say "Checking models..."
ensure_model "$MODEL" "reviewer"
ensure_model "$EMBED_MODEL" "embedding"

# --- 4. Write a valid config pointed at the local models --------------------
# Built from the package's own schema, so it always validates. We only swap the
# model name, bump the request timeout for large cold loads, and force
# think:false. context_window stays 8192 -> sent to Ollama as num_ctx.
say "Writing ollama-sentinel.yaml..."
if [[ -f ollama-sentinel.yaml ]]; then
  cp ollama-sentinel.yaml "ollama-sentinel.yaml.bak.$(date +%s)" 2>/dev/null || true
  warn "Existing config backed up."
fi
"$PYBIN" - "$MODEL" "$EMBED_MODEL" <<'PY'
import sys, yaml
from ollama_sentinel.config import create_default_config
model, embed = sys.argv[1], sys.argv[2]
c = create_default_config(".")
c["ollama"]["request_timeout"] = 600          # room for a large cold load
for role in c["ollama"]["models"].values():
    role["name"] = model
    role["think"] = False                      # reasoning models: skip <think>
c["embedding"]["models"]["hot"] = embed
with open("ollama-sentinel.yaml", "w") as f:
    yaml.safe_dump(c, f, sort_keys=False, default_flow_style=False)
print("wrote ollama-sentinel.yaml")
PY
# Actually validate the generated config loads against the schema.
"$PYBIN" -c "import sys; from ollama_sentinel.config import load_config; sys.exit(0 if load_config('ollama-sentinel.yaml') is not None else 1)" \
  || die "Generated config failed to validate. Leaving it in place for inspection."
ok "Config written + validated (reviewer=$MODEL, embedder=$EMBED_MODEL, context=8192)"

# --- 5. Pin Python so `ollama-sentinel` resolves in this directory ----------
if [[ -n "$PYVER" ]] && command -v pyenv >/dev/null 2>&1; then
  if [[ ! -f .python-version ]]; then
    pyenv local "$PYVER" 2>/dev/null && ok "Pinned Python $PYVER (.python-version)" \
      || warn "Could not pin Python; run from a dir under \$HOME or use: $SENTINEL_BIN"
  else
    ok ".python-version already present ($(cat .python-version))"
  fi
fi

# --- 6. Ignore review output -----------------------------------------------
if [[ -d .git || -f .gitignore ]]; then
  for pat in ".ollama_reviews/" ".ollama_violations.db*"; do
    grep -qxF "$pat" .gitignore 2>/dev/null || echo "$pat" >> .gitignore
  done
  ok "Added .ollama_reviews/ to .gitignore"
fi

# --- 7. Done ----------------------------------------------------------------
echo
ok "Setup complete in: $(pwd)"
cat <<EOF

Next:
  ollama-sentinel run                 # watch this folder, auto-review on save
  ollama-sentinel review FILE         # review one file now
  ollama-sentinel report              # recurring violations
  ollama-sentinel dashboard           # live TUI
  ollama-sentinel incidents           # corroborated events

First review triggers a cold model load (~20s for a 35B); it stays warm after.
EOF

if [[ "$START_RUN" -eq 1 ]]; then
  say "Starting watcher (Ctrl-C to stop)..."
  exec "$SENTINEL_BIN" run -c ollama-sentinel.yaml
fi
