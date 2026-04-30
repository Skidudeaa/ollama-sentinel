# Ollama Sentinel

A local AI development companion that remembers.

**The sentinel** watches your code directory, reviews every change with a local Ollama model, and builds a cumulative violation database that tracks recurring issues across your codebase. After a week it knows your blind spots. After a month it's indispensable.

**The research agent** produces dependency-aware impact analysis for library migrations, CVEs, and API changes -- not generic essays, but ranked lists of every call site in YOUR code that breaks, with severity and suggested fixes.

Both tools are local. Your code never leaves your machine.

## Quick Start

```bash
pip install -e .
ollama pull gemma3:4b && ollama serve   # need a running Ollama instance
ollama-sentinel init && ollama-sentinel run
```

Edit a file. A review appears in seconds. Run `ollama-sentinel report` after a few reviews to see your recurring violations ranked by frequency.

## Documentation

See **[docs/GUIDE.md](docs/GUIDE.md)** for the full user guide covering:
- Sentinel setup, commands, and configuration
- Violation memory and the `report` command
- Research agent installation and usage
- Impact analysis output format
- Project philosophy and architecture

## Commands

```bash
ollama-sentinel run                      # watch + auto-review
ollama-sentinel review file.py -m security   # manual review with model role
ollama-sentinel report                   # show recurring violations
ollama-sentinel report -f json           # machine-readable output
ollama-sentinel triage < pytest.log      # diagnose tool output via local model
ollama-sentinel dashboard                # live TUI of reviews + recurring violations
ollama-sentinel init                     # create config file

python -m research_agent.main query "question" --context src/ --output result.md
python -m research_agent.main interactive
```

## Requirements

- Python 3.10+
- [Ollama](https://ollama.ai) running locally (sentinel)
- `OPENAI_API_KEY` env var (research agent only)
- `pip install -e ".[research]"` for research agent dependencies

## Testing

```bash
pip install -e ".[dev]"
pytest tests/ -v   # 336 tests, ~3 seconds
```

## License

MIT
