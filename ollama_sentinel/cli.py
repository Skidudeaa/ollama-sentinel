"""
Command-line interface for Ollama Sentinel.
"""
import asyncio
import logging
import pathlib

import typer
import yaml
from rich.console import Console
from rich.logging import RichHandler

from . import __version__
from .config import create_default_config
from .processor import FileChange
from .watcher import FileSentinel
from watchfiles import Change

app = typer.Typer()
console = Console()

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
    )
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
        sentinel = FileSentinel(config_file)
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
    )
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
    
    async def run_review():
        sentinel = FileSentinel(config_file)
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
        table = Table(title=f"Recurring Violations (seen >= {min_count}x)")
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


if __name__ == "__main__":
    app()