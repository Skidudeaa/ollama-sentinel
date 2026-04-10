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


if __name__ == "__main__":
    app()