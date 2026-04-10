# research_agent/main.py
#!/usr/bin/env python
from __future__ import annotations
import os
import logging
from pathlib import Path
from typing import Optional

import click

from research_agent.core.agent import ResearchAgent
from research_agent.cli.interface import run_cli
from research_agent.core.logging import get_logger

logger = get_logger(__name__)


def read_file_content(file_path: str) -> tuple[Optional[str], bool]:
    """Read file content with comprehensive error handling.
    
    Args:
        file_path: Path to the file to read
        
    Returns:
        Tuple containing (file_content, error_occurred)
        Where file_content is None if an error occurred
    """
    content = None
    error_occurred = False
    
    try:
        with open(file_path, "r") as f:
            content = f.read()
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
        error_occurred = True
    except PermissionError:
        logger.error(f"Permission denied when reading file: {file_path}")
        error_occurred = True
    except IsADirectoryError:
        logger.error(f"Path is a directory, not a file: {file_path}")
        error_occurred = True
    except UnicodeDecodeError as e:
        logger.error(f"Encoding error when reading file {file_path}: {e}")
        error_occurred = True
    except Exception as e:
        logger.error(f"Unexpected error reading file {file_path}: {e}")
        error_occurred = True
        
    if error_occurred:
        logger.warning(f"Could not read content from {file_path}")
        
    return content, error_occurred


def write_file_content(file_path: str, content: str) -> bool:
    """Write content to file with comprehensive error handling.
    
    Args:
        file_path: Path to the output file
        content: Content to write to the file
        
    Returns:
        True if write was successful, False otherwise
    """
    try:
        with open(file_path, "w") as f:
            f.write(content)
        logger.info(f"Content written to {file_path}")
        return True
    except FileNotFoundError:
        logger.error(f"Output directory does not exist for file: {file_path}")
    except PermissionError:
        logger.error(f"Permission denied when writing to file: {file_path}")
    except IsADirectoryError:
        logger.error(f"Path is a directory, not a file: {file_path}")
    except Exception as e:
        logger.error(f"Unexpected error writing to file {file_path}: {e}")
    
    return False


def init_agent(repo: Path, config: Path) -> ResearchAgent:
    """Initialize the research agent with the specified configuration.
    
    Args:
        repo: Path to code repository to include in context
        config: Path to configuration file
        
    Returns:
        Initialized ResearchAgent instance
    """
    return ResearchAgent(
        repo_path=repo,
        config_path=config
    )

@click.group()
@click.option(
    "--repo", 
    default=os.getcwd(),
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    help="Path to code repository to include in context (default: current directory)"
)
@click.option(
    "--config", 
    default="config.toml",
    type=click.Path(exists=False, file_okay=True, dir_okay=False, path_type=Path),
    help="Path to configuration file (default: config.toml)"
)
@click.option(
    "--verbose/--quiet", 
    default=False,
    help="Enable verbose logging (default: False)"
)
@click.pass_context
def cli(ctx, repo, config, verbose):
    """Advanced research agent for gathering information from the web and code.
    
    Type 'research query "Your question here"' to run a one-shot query,
    or 'research interactive' to enter an interactive shell.
    """
    # Set up logging
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Initialize agent
    ctx.ensure_object(dict)
    ctx.obj["agent"] = init_agent(repo, config)

@cli.command()
@click.argument("question")
@click.option(
    "--context", 
    "-c",
    help="File containing code context to include"
)
@click.option(
    "--output", 
    "-o",
    help="Write output to file"
)
@click.pass_context
def query(ctx, question, context, output):
    """Execute a one-shot research query."""
    agent = ctx.obj["agent"]
    
    # Load code context if specified
    code_context = None
    if context:
        code_context, context_error = read_file_content(context)
        if context_error:
            logger.warning(f"Proceeding without context from {context}. Results may be less accurate.")
    
    # Execute query
    try:
        session = agent.research(question, code_context)
    except Exception as e:
        logger.error(f"Error during research: {e}")
        # Create a minimal session object to prevent downstream errors
        from types import SimpleNamespace
        session = SimpleNamespace()
        session.answer = f"Error during research: {e}"
        session.confidence = 0.0
        logger.error("Created fallback session due to research error")
    
    # Output results
    answer_text = getattr(session, 'answer', 'No answer available')
    confidence = getattr(session, 'confidence', None)
    confidence_line = f"\n\nConfidence: {confidence:.2f}" if confidence is not None else ""

    if output:
        content = f"{answer_text}{confidence_line}"
        success = write_file_content(output, content)
        if not success:
            logger.error(f"Failed to write results to {output}")
    else:
        print(answer_text)
        if confidence is not None:
            print(f"\nConfidence: {confidence:.2f}")
        else:
            print("\nConfidence: Not available")

@cli.command()
@click.pass_context
def interactive(ctx):
    """Run in interactive mode."""
    agent = ctx.obj["agent"]
    run_cli(agent)

@cli.command()
@click.pass_context
def setup(ctx):
    """Set up the research agent environment."""
    from research_agent.utils.setup import setup_environment
    setup_environment()

if __name__ == "__main__":
    cli(obj={})