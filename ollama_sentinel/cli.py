"""
Command-line interface for Ollama Sentinel.
"""
import asyncio
import logging
import pathlib
import sys
from typing import List, Optional

import typer
import yaml
from rich.console import Console
from rich.logging import RichHandler

from . import __version__
from .config import create_default_config
from .processor import FileChange
from .watcher import FileSentinel
from watchfiles import Change

app = typer.Typer(invoke_without_command=True)
console = Console()


def _is_stdin_tty() -> bool:
    return sys.stdin.isatty()


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"ollama-sentinel {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Ollama Sentinel — local-first AI code review companion."""
    if ctx.invoked_subcommand is not None:
        return

    config_path = "ollama-sentinel.yaml"

    config_file = pathlib.Path(config_path)
    if not config_file.exists():
        typer.echo(ctx.get_help())
        raise typer.Exit()

    from .config import load_config
    from .dashboard import run_dashboard

    config = load_config(config_file)
    if config is None:
        log.error("Failed to load configuration.")
        raise typer.Exit(code=1)

    watch_dir = pathlib.Path(config.watch.directory).resolve()
    reviews_dir = watch_dir / config.output.directory
    db_path = watch_dir / config.memory.db_path
    model_cfg = config.ollama.models.get("default")
    model_display = model_cfg.name if model_cfg else "unknown"

    try:
        asyncio.run(run_dashboard(
            watch_dir=watch_dir,
            reviews_dir=reviews_dir,
            db_path=db_path,
            config_path=str(config_file),
            model_name=model_display,
        ))
    except KeyboardInterrupt:
        pass

# Configure rich console and logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True, markup=True)]
)
log = logging.getLogger("ollama-sentinel")


@app.command()
def run(
    config_path: str = typer.Option(
        "ollama-sentinel.yaml",
        "--config",
        "-c",
        help="Path to configuration file"
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose logging"
    ),
    no_grounding: bool = typer.Option(
        False,
        "--no-grounding",
        help=(
            "Debug-only: disable schema-constrained output and verbatim-excerpt "
            "validation. Falls back to the legacy regex extractor on free-form "
            "prose. Use only for comparing grounded vs ungrounded model output."
        ),
    ),
):
    """Run the Ollama Sentinel service."""
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    log.info(f"Ollama Sentinel v{__version__}")

    config_file = pathlib.Path(config_path)
    if not config_file.exists():
        log.error(f"Configuration file not found: {config_file}")
        raise typer.Exit(code=1)

    try:
        grounding_override = False if no_grounding else None
        sentinel = FileSentinel(config_file, grounding_override=grounding_override)
        if no_grounding:
            log.warning("--no-grounding: schema-constrained output disabled; using legacy regex extractor")
        asyncio.run(sentinel.run())
    except KeyboardInterrupt:
        log.info("Stopped by user")
    except Exception as e:
        log.error(f"Error running sentinel: {e}")
        raise typer.Exit(code=1)


@app.command()
def review(
    path: str = typer.Argument(..., help="Path to file to review"),
    config_path: str = typer.Option(
        "ollama-sentinel.yaml",
        "--config",
        "-c",
        help="Path to configuration file"
    ),
    model: str = typer.Option(
        "default",
        "--model",
        "-m",
        help="Model role to use for review"
    ),
    no_grounding: bool = typer.Option(
        False,
        "--no-grounding",
        help=(
            "Debug-only: disable schema-constrained output and verbatim-excerpt "
            "validation. Falls back to the legacy regex extractor on free-form "
            "prose. Use only for comparing grounded vs ungrounded model output."
        ),
    ),
):
    """Manually review a single file."""
    config_file = pathlib.Path(config_path)
    if not config_file.exists():
        log.error(f"Configuration file not found: {config_file}")
        raise typer.Exit(code=1)

    file_path = pathlib.Path(path).resolve()

    if not file_path.is_file():
        log.error(f"File not found: {file_path}")
        raise typer.Exit(code=1)

    grounding_override = False if no_grounding else None
    if no_grounding:
        log.warning("--no-grounding: schema-constrained output disabled; using legacy regex extractor")

    async def run_review():
        sentinel = FileSentinel(config_file, grounding_override=grounding_override)
        file_change = FileChange(path=file_path, change_type=Change.modified)
        await sentinel.process_change(file_change, model_role=model)
        await sentinel.processor.close()
    
    try:
        asyncio.run(run_review())
    except KeyboardInterrupt:
        log.info("Stopped by user")
    except Exception as e:
        log.error(f"Error reviewing file: {e}")
        raise typer.Exit(code=1)


@app.command()
def init(
    directory: str = typer.Argument(
        ".",
        help="Directory to watch"
    ),
    output: str = typer.Option(
        ".ollama_reviews",
        "--output",
        "-o",
        help="Output directory for reviews"
    )
):
    """Initialize a new configuration file."""
    config_path = pathlib.Path("ollama-sentinel.yaml")
    
    if config_path.exists():
        overwrite = typer.confirm(f"{config_path} already exists. Overwrite?")
        if not overwrite:
            raise typer.Exit()
    
    # Create a basic configuration
    config = create_default_config(directory, output)
    
    with open(config_path, "w") as f:
        yaml.dump(config, f, sort_keys=False, default_flow_style=False)
    
    log.info(f"Created configuration file: {config_path}")


@app.command()
def report(
    config_path: str = typer.Option(
        "ollama-sentinel.yaml",
        "--config",
        "-c",
        help="Path to configuration file",
    ),
    min_count: int = typer.Option(
        2,
        "--min-count",
        "-n",
        help="Minimum occurrence count to include",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        "-l",
        help="Maximum number of violations to show",
    ),
    output_format: str = typer.Option(
        "table",
        "--format",
        "-f",
        help="Output format: table or json",
    ),
):
    """Show recurring code review violations ranked by frequency."""
    import json as json_mod

    from rich.table import Table

    from .config import load_config
    from .violation_db import ViolationDB

    config_file = pathlib.Path(config_path)
    if not config_file.exists():
        log.error(f"Configuration file not found: {config_file}")
        raise typer.Exit(code=1)

    config = load_config(config_file)
    if not config:
        log.error("Failed to load configuration")
        raise typer.Exit(code=1)

    db_path = pathlib.Path(config.watch.directory).resolve() / config.memory.db_path
    if not db_path.exists():
        console.print("[yellow]No violation database found. Run some reviews first.[/yellow]")
        raise typer.Exit()

    db = ViolationDB(str(db_path))
    try:
        violations = db.get_recurring(min_count=min_count, limit=limit)
    finally:
        db.close()

    if not violations:
        console.print("[green]No recurring violations found.[/green]")
        raise typer.Exit()

    if output_format == "json":
        console.print(json_mod.dumps(violations, indent=2))
    else:
        table = Table(title=f"Patterns (seen >= {min_count}x)")
        table.add_column("#", style="dim", width=4)
        table.add_column("Count", style="bold red", width=6)
        table.add_column("Severity", width=10)
        table.add_column("Category", width=12)
        table.add_column("File", style="cyan")
        table.add_column("Lines", width=8)
        table.add_column("Description")

        for i, v in enumerate(violations, 1):
            table.add_row(
                str(i),
                str(v["occurrence_count"]),
                v["severity"],
                v["category"],
                v["file_path"],
                f"{v['line_start']}-{v['line_end']}",
                v["description"][:60],
            )
        console.print(table)


def _load_config_or_exit(config_path: str):
    """Shared: resolve + load the YAML config or exit(1)."""
    from .config import load_config

    config_file = pathlib.Path(config_path)
    if not config_file.exists():
        log.error(f"Configuration file not found: {config_file}")
        raise typer.Exit(code=1)
    config = load_config(config_file)
    if not config:
        log.error("Failed to load configuration")
        raise typer.Exit(code=1)
    return config


@app.command(name="install-hooks")
def install_hooks_cmd(
    config_path: str = typer.Option(
        "ollama-sentinel.yaml", "--config", "-c",
        help="Path to configuration file",
    ),
):
    """Install the git post-commit hook into the watched repository."""
    from .hooks import install_hooks

    config = _load_config_or_exit(config_path)
    repo_path = pathlib.Path(config.watch.directory).resolve()
    try:
        installed = install_hooks(repo_path)
    except FileNotFoundError as e:
        log.error(str(e))
        raise typer.Exit(code=1)

    if installed:
        console.print(
            f"[green]Installed git hook(s): {', '.join(installed)}[/green]"
        )
    else:
        console.print(
            "[yellow]post-commit hook already exists — left untouched.[/yellow]"
        )


@app.command(name="record-commit")
def record_commit_cmd(
    config_path: str = typer.Option(
        "ollama-sentinel.yaml", "--config", "-c",
        help="Path to configuration file",
    ),
    commit_sha: Optional[str] = typer.Option(
        None, "--commit", help="Commit SHA to link (default: HEAD)",
    ),
):
    """Link a commit to open Findings in the files it touched.

    Called by the post-commit git hook; also usable manually.
    """
    from .hooks import record_commit
    from .violation_db import ViolationDB

    config = _load_config_or_exit(config_path)
    repo_path = pathlib.Path(config.watch.directory).resolve()
    db_path = repo_path / config.memory.db_path
    if not db_path.exists():
        log.info("No violation database yet — nothing to link.")
        raise typer.Exit()

    db = ViolationDB(str(db_path))
    try:
        linked = record_commit(repo_path, db, commit_sha=commit_sha)
    finally:
        db.close()
    log.info("Linked %d finding(s) to the commit.", linked)


@app.command()
def confirm(
    finding_id: int = typer.Argument(
        ..., help="ID of the Finding to confirm"
    ),
    config_path: str = typer.Option(
        "ollama-sentinel.yaml", "--config", "-c",
        help="Path to configuration file",
    ),
    note: str = typer.Option(
        "", "--note", "-n",
        help="Optional context for the confirmation",
    ),
):
    """Manually confirm a Finding, promoting it to an Incident.

    Creates an Incident with confirming_signal='manual_confirm'. The
    Finding stays open — confirmation is corroboration, not resolution.
    """
    import sqlite3

    from .violation_db import Incident, ViolationDB

    config = _load_config_or_exit(config_path)
    repo_path = pathlib.Path(config.watch.directory).resolve()
    db_path = repo_path / config.memory.db_path
    if not db_path.exists():
        console.print("[red]No violation database found.[/red]")
        raise typer.Exit(code=1)

    db = ViolationDB(str(db_path))
    try:
        artifact = note or "manual confirm via `ollama-sentinel confirm`"
        try:
            db.persist_incident(
                Incident(
                    finding_id=finding_id,
                    confirming_signal="manual_confirm",
                    confirming_artifact=artifact,
                )
            )
        except sqlite3.IntegrityError:
            console.print(
                f"[red]No finding with id {finding_id}; "
                f"nothing to confirm.[/red]"
            )
            raise typer.Exit(code=1)
    finally:
        db.close()

    console.print(
        f"[green]Confirmed finding {finding_id} — Incident recorded "
        f"(finding stays open).[/green]"
    )


@app.command()
def incidents(
    config_path: str = typer.Option(
        "ollama-sentinel.yaml", "--config", "-c",
        help="Path to configuration file",
    ),
    days: int = typer.Option(
        30, "--days", "-d", help="Look back this many days (ignored with --finding)",
    ),
    finding_id: Optional[int] = typer.Option(
        None, "--finding", help="Show only incidents for this Finding id",
    ),
    output_format: str = typer.Option(
        "table", "--format", "-f", help="Output format: table or json",
    ),
):
    """Show recent Incidents — corroborated events linked to Findings.

    Incidents are objective events (test failures, manual confirmations,
    fix commits) that corroborate a model Finding. Pass --finding to scope
    to one Finding; otherwise the most recent incidents within --days show.
    """
    import json as json_mod

    from rich.table import Table

    from .violation_db import ViolationDB

    config = _load_config_or_exit(config_path)
    db_path = pathlib.Path(config.watch.directory).resolve() / config.memory.db_path
    if not db_path.exists():
        console.print(
            "[yellow]No violation database found. Run some reviews first.[/yellow]"
        )
        raise typer.Exit()

    db = ViolationDB(str(db_path))
    try:
        if finding_id is not None:
            records = db.get_incidents_for_finding(finding_id)
        else:
            records = db.get_recent_incidents(days=days, limit=50)
    finally:
        db.close()

    if not records:
        console.print("[green]No incidents recorded yet.[/green]")
        raise typer.Exit()

    if output_format == "json":
        console.print(json_mod.dumps(records, indent=2))
        return

    scope = (
        f"finding {finding_id}" if finding_id is not None
        else f"last {days}d"
    )
    table = Table(title=f"Incidents ({scope})")
    table.add_column("#", style="dim", width=4)
    table.add_column("Finding", style="bold", width=8)
    table.add_column("Signal", width=14)
    table.add_column("Symptom", style="cyan")
    table.add_column("Artifact")
    table.add_column("When", style="dim", width=12)

    for i, inc in enumerate(records, 1):
        symptom = (
            f"{inc['symptom_file']}:{inc['symptom_line']}"
            if inc.get("symptom_file") else "—"
        )
        when = (inc.get("created_at") or "")[:10]
        table.add_row(
            str(i),
            str(inc["finding_id"]),
            inc["confirming_signal"],
            symptom,
            (inc.get("confirming_artifact") or "")[:50],
            when,
        )
    console.print(table)


@app.command()
def findings(
    config_path: str = typer.Option(
        "ollama-sentinel.yaml", "--config", "-c",
        help="Path to configuration file",
    ),
    severity: Optional[str] = typer.Option(
        None, "--severity", help="Filter by exact severity (e.g. high)",
    ),
    file_substr: Optional[str] = typer.Option(
        None, "--file", help="Filter by file-path substring (case-insensitive)",
    ),
    limit: int = typer.Option(
        50, "--limit", "-l", help="Maximum number of findings to show",
    ),
    output_format: str = typer.Option(
        "table", "--format", "-f", help="Output format: table or json",
    ),
):
    """List open (unresolved) findings with their ids for resolve/dismiss."""
    import json as json_mod

    from rich.table import Table

    from .violation_db import ViolationDB

    config = _load_config_or_exit(config_path)
    db_path = pathlib.Path(config.watch.directory).resolve() / config.memory.db_path
    if not db_path.exists():
        console.print(
            "[yellow]No violation database found. Run some reviews first.[/yellow]"
        )
        raise typer.Exit()

    db = ViolationDB(str(db_path))
    try:
        rows = db.get_open_findings(
            severity=severity, file_substr=file_substr, limit=limit,
        )
        corroborated: set = set()
        if rows:
            paths = sorted({r["file_path"] for r in rows})
            corroborated = {
                r["id"] for r in db.get_findings_with_incidents(paths)
            }
    finally:
        db.close()

    if not rows:
        console.print("[green]No open findings.[/green]")
        raise typer.Exit()

    if output_format == "json":
        console.print(json_mod.dumps(rows, indent=2))
        return

    table = Table(title=f"Open findings ({len(rows)})")
    table.add_column("ID", style="bold", width=5)
    table.add_column("Sev", width=9)
    table.add_column("Cat", width=10)
    table.add_column("Location", style="cyan")
    table.add_column("Count", width=6)
    table.add_column("Corr", width=5)
    table.add_column("Description")

    for r in rows:
        table.add_row(
            str(r["id"]),
            r["severity"],
            r["category"],
            f"{r['file_path']}:{r['line_start']}",
            str(r["occurrence_count"]),
            "✓" if r["id"] in corroborated else "",
            (r["description"] or "")[:60],
        )
    console.print(table)


@app.command()
def surface(
    config_path: str = typer.Option(
        "ollama-sentinel.yaml", "--config", "-c",
        help="Path to configuration file",
    ),
    output: Optional[str] = typer.Option(
        None, "--output", "-o",
        help="SARIF output path (default: <reviews-dir>/findings.sarif)",
    ),
):
    """Emit open findings as SARIF for editor Problems panels and CI.

    Findings are re-anchored to their current line by verbatim excerpt;
    stale findings (excerpt no longer present) are reported but excluded.
    Read-only: never edits source, never changes finding state.
    """
    from .sarif import generate_sarif_file
    from .violation_db import ViolationDB

    config = _load_config_or_exit(config_path)
    watch_dir = pathlib.Path(config.watch.directory).resolve()
    db_path = watch_dir / config.memory.db_path
    if not db_path.exists():
        console.print(
            "[yellow]No violation database found. Run some reviews first.[/yellow]"
        )
        raise typer.Exit()

    output_dir = watch_dir / config.output.directory
    out_path = pathlib.Path(output).resolve() if output else None

    db = ViolationDB(str(db_path))
    try:
        summary = generate_sarif_file(
            db, watch_dir, output_dir,
            tool_version=__version__, out_path=out_path,
        )
    finally:
        db.close()

    console.print(
        f"[green]Wrote {summary.emitted} findings → {summary.path}[/green] "
        f"[dim]({summary.relocated} relocated, "
        f"{summary.unverified} unverified, {summary.stale} stale)[/dim]"
    )


@app.command()
def triage(
    input_path: Optional[str] = typer.Argument(
        None,
        metavar="[INPUT]",
        help="Path to a log/output file. Omit to read stdin.",
    ),
    config_path: str = typer.Option(
        "ollama-sentinel.yaml",
        "--config",
        "-c",
        help="Path to configuration file",
    ),
    model: str = typer.Option(
        "triage",
        "--model",
        "-m",
        help='Model role (default: "triage"; auto-fallback to "default" if missing)',
    ),
    output_path: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="Save triage output to this file in addition to printing",
    ),
    context: List[str] = typer.Option(
        [],
        "--context",
        help="Additional source file to include (repeatable)",
    ),
    no_extract: bool = typer.Option(
        False,
        "--no-extract",
        help="Disable auto-extraction of referenced file paths",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Debug logging",
    ),
):
    """Diagnose terminal output (tracebacks, lints, failed tests) with the local model."""
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    from .config import load_config
    from .triage import run_triage

    # --- Resolve input text.
    if input_path:
        path = pathlib.Path(input_path)
        if not path.is_file():
            log.error(f"Cannot read {input_path}: file not found")
            raise typer.Exit(code=1)
        try:
            input_text = path.read_text(errors="replace")
        except OSError as e:
            log.error(f"Cannot read {input_path}: {e}")
            raise typer.Exit(code=1)
    else:
        if _is_stdin_tty():
            log.error("No input — pipe tool output or pass a path.")
            raise typer.Exit(code=1)
        input_text = sys.stdin.read()

    if not input_text.strip():
        log.error("Empty input; nothing to triage.")
        raise typer.Exit(code=1)

    # --- Load config.
    config_file = pathlib.Path(config_path)
    if not config_file.exists():
        log.error(f"Configuration file not found: {config_file}")
        raise typer.Exit(code=1)
    config = load_config(config_file)
    if config is None:
        log.error("Failed to load configuration.")
        raise typer.Exit(code=1)

    # --- Resolve --context paths.
    context_paths: list[pathlib.Path] = []
    for raw in context:
        p = pathlib.Path(raw).resolve()
        if not p.is_file():
            log.error(f"--context file not found: {raw}")
            raise typer.Exit(code=1)
        context_paths.append(p)

    cwd = pathlib.Path.cwd().resolve()

    # --- Run.
    try:
        result = asyncio.run(run_triage(
            input_text=input_text,
            config=config,
            cwd=cwd,
            model_role=model,
            explicit_context=context_paths,
            extract=not no_extract,
        ))
    except KeyError as e:
        log.error(str(e))
        raise typer.Exit(code=1)
    except Exception as e:
        log.error(f"Triage failed: {e}")
        raise typer.Exit(code=2)

    # --- Render & save.
    if console.is_terminal:
        try:
            from rich.markdown import Markdown
            console.print(Markdown(result))
        except Exception:
            print(result)
    else:
        print(result)

    if output_path:
        try:
            pathlib.Path(output_path).write_text(result)
        except OSError as e:
            log.error(f"Failed to save --output {output_path}: {e}")
            raise typer.Exit(code=1)


@app.command()
def dashboard(
    config_path: str = typer.Option(
        "ollama-sentinel.yaml",
        "--config",
        "-c",
        help="Path to configuration file",
    ),
    refresh: float = typer.Option(
        1.0,
        "--refresh",
        "-r",
        help="Seconds between refreshes",
    ),
    min_count: int = typer.Option(
        2,
        "--min-count",
        "-n",
        help="Minimum occurrence count for Patterns panel",
    ),
):
    """Live Control Center dashboard for a running sentinel (read-only)."""
    from .config import load_config
    from .dashboard import run_dashboard

    config_file = pathlib.Path(config_path)
    if not config_file.exists():
        log.error(f"Configuration file not found: {config_file}")
        raise typer.Exit(code=1)

    config = load_config(config_file)
    if config is None:
        log.error("Failed to load configuration.")
        raise typer.Exit(code=1)

    watch_dir = pathlib.Path(config.watch.directory).resolve()
    reviews_dir = watch_dir / config.output.directory
    db_path = watch_dir / config.memory.db_path
    model_cfg = config.ollama.models.get("default")
    model_display = model_cfg.name if model_cfg else "unknown"

    try:
        asyncio.run(run_dashboard(
            watch_dir=watch_dir,
            reviews_dir=reviews_dir,
            db_path=db_path,
            refresh_s=refresh,
            min_count=min_count,
            config_path=str(config_file),
            model_name=model_display,
        ))
    except KeyboardInterrupt:
        pass


@app.command()
def research(
    query: Optional[str] = typer.Argument(
        None,
        help="Research query. Omit to enter interactive mode.",
    ),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        "-i",
        help="Enter interactive research REPL",
    ),
    config_path: str = typer.Option(
        "ollama-sentinel.yaml",
        "--config",
        "-c",
        help="Path to configuration file",
    ),
    context: Optional[str] = typer.Option(
        None,
        "--context",
        help="Path to a source file for code context",
    ),
    output_path: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="Save research answer to this file",
    ),
):
    """Research a question using web search, code analysis, and synthesis."""
    from .research_bridge import is_available

    if not is_available():
        console.print(
            "[bold red]Research extras not installed.[/]\n\n"
            "Install with: [bold]pip install -e \".[research]\"[/]"
        )
        raise typer.Exit(code=1)

    from .config import load_config

    config_file = pathlib.Path(config_path)
    config = load_config(config_file) if config_file.exists() else None

    # Resolve paths from config or defaults
    if config:
        watch_dir = pathlib.Path(config.watch.directory).resolve()
        repo_path = (
            pathlib.Path(config.research.repo_path).resolve()
            if config.research.repo_path
            else watch_dir
        )
        research_config = (
            pathlib.Path(config.research.config_path)
            if config.research.config_path
            else None
        )
        output_dir = watch_dir / config.output.directory
    else:
        repo_path = pathlib.Path.cwd()
        research_config = None
        output_dir = None

    if interactive or query is None:
        from .research_bridge import run_interactive
        try:
            run_interactive(repo_path, research_config)
        except KeyboardInterrupt:
            pass
        return

    # One-shot query
    code_context = None
    if context:
        ctx_path = pathlib.Path(context)
        if not ctx_path.is_file():
            log.error(f"Context file not found: {context}")
            raise typer.Exit(code=1)
        code_context = ctx_path.read_text(errors="replace")

    from .research_bridge import run_query, persist_latest

    try:
        result = run_query(
            query=query,
            repo_path=repo_path,
            config_path=research_config,
            code_context=code_context,
        )
    except Exception as e:
        log.error(f"Research failed: {e}")
        raise typer.Exit(code=2)

    # Render answer
    try:
        from rich.markdown import Markdown
        console.print(Markdown(result["answer"]))
    except Exception:
        print(result["answer"])

    conf = result.get("confidence", 0)
    console.print(f"\n[dim]Confidence: {conf:.0%} | Sources: {result.get('source_count', 0)}[/]")

    # Persist for Control Center
    if output_dir:
        persist_latest(result, output_dir)

    # Save to file if requested
    if output_path:
        try:
            pathlib.Path(output_path).write_text(result["answer"])
        except OSError as e:
            log.error(f"Failed to write output: {e}")
            raise typer.Exit(code=1)


if __name__ == "__main__":
    app()