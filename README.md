# Ollama Sentinel

Automated code reviews with local AI models, plus a multi-step research agent.

## Modules

**ollama_sentinel** — Watches a directory for file changes and sends modified files to a local Ollama instance for AI-powered code review. Reviews are saved as versioned markdown/JSON/HTML files.

**research_agent** — A multi-step research agent using LangGraph that orchestrates web search, browser scraping, code indexing, synthesis, and verification through an iterative workflow.

## Prerequisites

- Python 3.10+
- A running [Ollama](https://ollama.ai) instance at `http://localhost:11434` (configurable)
- For research_agent: `OPENAI_API_KEY` env var (required), `SERPAPI_API_KEY` (optional)

## Installation

```bash
git clone https://github.com/skidudeaa/ollama-sentinel.git
cd ollama-sentinel

# Install sentinel only
pip install -e .

# Install with research agent dependencies
pip install -e ".[research]"

# Install with dev/test dependencies
pip install -e ".[dev]"
```

## Usage — Ollama Sentinel

```bash
# Initialize a config file in the current directory
ollama-sentinel init

# Watch for file changes and auto-review
ollama-sentinel run
ollama-sentinel run -c path/to/config.yaml   # custom config
ollama-sentinel run -v                       # verbose logging

# Review a single file
ollama-sentinel review path/to/file.py
ollama-sentinel review path/to/file.py -m security   # use a specific model role
```

## Usage — Research Agent

```bash
# Run a research query
python -m research_agent.main query "your question" --context file.py --output result.md

# Interactive research session
python -m research_agent.main interactive

# Check environment setup
python -m research_agent.main setup
```

## Configuration

### Sentinel (YAML)

See `ollama-sentinel.yaml` for a full example. Key sections:

```yaml
watch:
  directory: "."
  recursive: true
  ignore_patterns: ["*.md", "*.log", "**/.git/**"]
  debounce_ms: 1500

ollama:
  host: "http://localhost:11434"
  models:
    default:
      name: "gemma3:4b"
      system_prompt: "You are a senior code reviewer..."
      temperature: 0.1

processing:
  max_chars_per_chunk: 12000
  max_concurrent_reviews: 3
  git_diff_mode: false

output:
  directory: ".ollama_reviews"
  format: "markdown"
```

### Research Agent (TOML + env vars)

Key environment variables:
- `OPENAI_API_KEY` — Required for LLM calls
- `SERPAPI_API_KEY` — Optional, enables SerpAPI search (falls back to DuckDuckGo)
- `RESEARCH_MODEL` — Override the OpenAI model (default: gpt-4)

## Testing

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT
