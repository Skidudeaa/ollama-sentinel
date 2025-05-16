# Ollama Sentinel

Automated code reviews with local AI models.

## Features

- Continuously watches your code directory for changes
- Sends changed files to a local Ollama model for review
- Provides code quality feedback, bug detection, and improvement suggestions
- Stores reviews as markdown files for easy reference
- Cross-platform (Linux, macOS, Windows)

## Installation

```bash
# Install from source
git clone https://github.com/skidudeaa/ollama-sentinel.git
cd ollama-sentinel
pip install -e .