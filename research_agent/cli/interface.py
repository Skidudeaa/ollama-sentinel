# research_agent/cli/interface.py
from __future__ import annotations
import logging
import os
import json
import time
from typing import Optional, Dict, Any, List
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

from research_agent.core.agent import ResearchAgent
from research_agent.core.models import ResearchSession, ContentItem
from research_agent.cli.history import HistoryManager
from research_agent.core.logging import get_logger

logger = get_logger(__name__)

class EnhancedCLI:
    """Enhanced CLI for research agent with rich formatting."""
    
    def __init__(
        self, 
        agent: ResearchAgent,
        enable_rich: bool = True,
        history_file: Optional[str] = None,
        max_history: int = 100,
        show_snippets: bool = True
    ):
        """Initialize CLI.
        
        Args:
            agent: Research agent
            enable_rich: Enable rich formatting
            history_file: Path to history file
            max_history: Maximum history items
            show_snippets: Show source snippets in results
        """
        self.agent = agent
        self.enable_rich = enable_rich
        self.show_snippets = show_snippets
        
        # Set up console
        self.console = Console()
        
        # Set up history
        self.history = HistoryManager(
            filepath=history_file or "~/.research_history",
            max_items=max_history
        )
    
    def _display_welcome(self):
        """Display welcome message."""
        if self.enable_rich:
            self.console.print(Panel.fit(
                "[bold green]Research Agent CLI[/bold green]\n"
                "Type [bold]'exit'[/bold] to quit, [bold]'help'[/bold] for commands, or enter your research query.",
                title="Welcome",
                border_style="green"
            ))
        else:
            print("Research Agent CLI")
            print("Type 'exit' to quit, 'help' for commands, or enter your research query.")
            print("-" * 40)
    
    def _display_help(self):
        """Display help information."""
        if self.enable_rich:
            table = Table(title="Commands")
            table.add_column("Command", style="cyan")
            table.add_column("Description")
            
            table.add_row("help", "Show this help message")
            table.add_row("exit/quit", "Exit the CLI")
            table.add_row("history", "Show search history")
            table.add_row("clear", "Clear the screen")
            table.add_row("config", "Show current configuration")
            table.add_row("!<number>", "Repeat query from history")
            
            self.console.print(table)
        else:
            print("Commands:")
            print("  help              Show this help message")
            print("  exit/quit         Exit the CLI")
            print("  history           Show search history")
            print("  clear             Clear the screen")
            print("  config            Show current configuration")
            print("  !<number>         Repeat query from history")
    
    def _display_history(self):
        """Display search history."""
        history_items = self.history.get_items()
        
        if not history_items:
            self.console.print("[yellow]No history items[/yellow]")
            return
            
        if self.enable_rich:
            table = Table(title="Search History")
            table.add_column("#", style="cyan", justify="right")
            table.add_column("Query")
            table.add_column("Time", style="green")
            table.add_column("Confidence", style="yellow")
            
            for i, item in enumerate(history_items):
                table.add_row(
                    str(i),
                    item.get("query", ""),
                    item.get("time", ""),
                    f"{item.get('confidence', 0):.2f}"
                )
                
            self.console.print(table)
        else:
            print("Search History:")
            for i, item in enumerate(history_items):
                print(f"  {i}: {item.get('query', '')} ({item.get('time', '')})")
    
    def _display_config(self):
        """Display current configuration."""
        config = self.agent.config.as_dict()
        
        if self.enable_rich:
            table = Table(title="Configuration")
            table.add_column("Setting", style="cyan")
            table.add_column("Value")
            
            # Flatten config for display
            flat_config = {}
            
            def flatten(prefix, d):
                for k, v in d.items():
                    key = f"{prefix}.{k}" if prefix else k
                    if isinstance(v, dict):
                        flatten(key, v)
                    else:
                        # Mask API keys
                        if "api_key" in key and v:
                            v = v[:5] + "..." + v[-2:]
                        flat_config[key] = str(v)
            
            flatten("", config)
            
            # Sort and display
            for k in sorted(flat_config.keys()):
                table.add_row(k, flat_config[k])
                
            self.console.print(table)
        else:
            print("Configuration:")
            
            # Flatten config for display
            flat_config = {}
            
            def flatten(prefix, d):
                for k, v in d.items():
                    key = f"{prefix}.{k}" if prefix else k
                    if isinstance(v, dict):
                        flatten(key, v)
                    else:
                        # Mask API keys
                        if "api_key" in key and v:
                            v = v[:5] + "..." + v[-2:]
                        flat_config[key] = str(v)
            
            flatten("", config)
            
            # Sort and display
            for k in sorted(flat_config.keys()):
                print(f"  {k} = {flat_config[k]}")
    
    def _display_research_progress(self, session: ResearchSession) -> ResearchSession:
        """Display research progress with spinner."""
        if not self.enable_rich:
            print(f"Researching: {session.query}")
            print("Working...", end="", flush=True)
            
            while not session.end_time:
                time.sleep(0.5)
                print(".", end="", flush=True)
                
            print("\nDone!")
            return session
        
        # Rich progress display
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold green]{task.description}"),
            BarColumn(),
            TextColumn("[bold]{task.fields[status]}"),
            TimeElapsedColumn(),
            console=self.console
        ) as progress:
            # Create task
            task = progress.add_task(f"Researching: {session.query}", status="Analyzing...", total=100)
            
            # Update progress based on steps
            steps = ["analyze", "search", "read", "code_search", "synthesize", "verify", "finalize"]
            total_steps = len(steps)
            completed_steps = 0
            
            # Poll for updates
            while not session.end_time:
                # Count completed steps
                completed = 0
                current_step = "analyzing"
                
                for step_name in steps:
                    step = session.get_step(step_name)
                    if step and step.status == "completed":
                        completed += 1
                    elif step and step.status == "running":
                        current_step = step_name
                
                # Calculate progress
                progress_pct = (completed / total_steps) * 100
                
                # Update progress
                progress.update(
                    task, 
                    completed=progress_pct,
                    status=f"{current_step.capitalize()}..."
                )
                
                # Sleep briefly
                time.sleep(0.2)
            
            # Ensure 100% at completion
            progress.update(task, completed=100, status="Complete!")
            
        return session
    
    def _display_results(self, session: ResearchSession):
        """Display research results."""
        # Add to history
        self.history.add_item({
            "query": session.query,
            "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(session.start_time)),
            "confidence": session.confidence,
            "duration": f"{session.duration:.1f}s"
        })
        
        if self.enable_rich:
            # Display answer
            self.console.print(Panel(
                Markdown(session.answer),
                title=f"Answer (confidence: {session.confidence:.2f})",
                border_style="green" if session.confidence >= 0.7 else "yellow"
            ))
            
            # Display sources if requested
            if self.show_snippets and session.sources:
                self.console.print("[bold]Sources:[/bold]")
                
                for i, source in enumerate(session.sources):
                    snippet = source.content[:200] + "..." if len(source.content) > 200 else source.content
                    self.console.print(Panel(
                        f"[link={source.url}]{source.title or source.url}[/link]\n\n{snippet}",
                        title=f"Source {i+1}",
                        border_style="blue"
                    ))
        else:
            # Simple display
            print(f"\nAnswer (confidence: {session.confidence:.2f}):")
            print("-" * 40)
            print(session.answer)
            print("-" * 40)
            
            # Display sources if requested
            if self.show_snippets and session.sources:
                print("\nSources:")
                
                for i, source in enumerate(session.sources):
                    snippet = source.content[:200] + "..." if len(source.content) > 200 else source.content
                    print(f"\nSource {i+1}: {source.title or source.url}")
                    print(f"URL: {source.url}")
                    print(f"Snippet: {snippet}")
    
    def run(self):
        """Run the CLI loop."""
        self._display_welcome()
        
        while True:
            try:
                # Get input
                if self.enable_rich:
                    query = self.console.input("\n[bold green]>[/bold green] ")
                else:
                    query = input("\n> ")
                    
                query = query.strip()
                
                # Check for commands
                if not query:
                    continue
                    
                if query.lower() in ("exit", "quit"):
                    break
                    
                if query.lower() == "help":
                    self._display_help()
                    continue
                    
                if query.lower() == "history":
                    self._display_history()
                    continue
                    
                if query.lower() == "clear":
                    self.console.clear() if self.enable_rich else os.system("cls" if os.name == "nt" else "clear")
                    continue
                    
                if query.lower() == "config":
                    self._display_config()
                    continue
                
                # Check for history reference
                if query.startswith("!"):
                    try:
                        index = int(query[1:])
                        history_items = self.history.get_items()
                        if 0 <= index < len(history_items):
                            query = history_items[index]["query"]
                        else:
                            self.console.print("[red]Invalid history index[/red]")
                            continue
                    except ValueError:
                        self.console.print("[red]Invalid history reference[/red]")
                        continue
                
                # Execute research query
                session = ResearchSession(query=query)
                
                # Start research in background and display progress
                research_task = self.agent.research(query)
                result = self._display_research_progress(research_task)
                
                # Display results
                self._display_results(result)
                
            except KeyboardInterrupt:
                if self.enable_rich:
                    self.console.print("\n[yellow]Operation cancelled[/yellow]")
                else:
                    print("\nOperation cancelled")
                    
            except Exception as e:
                if self.enable_rich:
                    self.console.print(f"[red]Error: {str(e)}[/red]")
                else:
                    print(f"Error: {str(e)}")
                logger.error(f"CLI error: {e}", exc_info=True)

def run_cli(agent: ResearchAgent):
    """Run the CLI."""
    # Get configuration
    enable_rich = agent.config.get("cli.enable_rich_formatting", True)
    history_file = agent.config.get("cli.history_file", "~/.research_history")
    max_history = agent.config.get("cli.max_history_items", 100)
    show_snippets = agent.config.get("cli.show_source_snippets", True)
    
    # Create and run CLI
    cli = EnhancedCLI(
        agent=agent,
        enable_rich=enable_rich,
        history_file=history_file,
        max_history=max_history,
        show_snippets=show_snippets
    )
    
    cli.run()