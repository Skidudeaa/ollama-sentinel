# Ollama Sentinel — User Manual

This manual tells you how to use Ollama Sentinel. Each section is one task. Do the steps in the given order.

For the visual overview, open [`index.html`](index.html) in a browser. For the design and the internal parts, read [`VISION.md`](VISION.md) and the project `CLAUDE.md`.

---

## 1. What the sentinel does

The sentinel sends your code to a local AI model for review. The model is on your computer, so your code stays on your computer.

The model reports problems in your code. This manual calls each problem a **finding**. The sentinel keeps all findings in a database. When the model sees the same problem again, the next review tells the model about it.

You can use the sentinel in three ways:

| Way | Command | When to use it |
|---|---|---|
| Watch mode | `ollama-sentinel run` | You want a review each time you save a file. |
| Check mode | `ollama-sentinel check` | You want a review of your changed files before you commit. |
| Single-file mode | `ollama-sentinel review FILE` | You want a review of one file now. |

### Words in this manual

| Word | Meaning |
|---|---|
| **finding** | One problem that the model reports. Each finding has a number (the finding ID). |
| **open finding** | A finding that is not closed yet. |
| **close** | Remove a finding from the open list. You close a finding with `resolve`, `dismiss`, or `prune`. |
| **severity** | How bad a finding is: `critical`, `high`, `medium`, or `low`. |
| **config file** | The file `ollama-sentinel.yaml` in your project folder. |
| **project folder** | The folder that contains your code and the config file. |
| **Control Center** | The full-screen view in your terminal that shows all findings. |
| **guardrail** | A rule that you write. The model checks each review against your guardrails. |

---

## 2. Install the sentinel (one time only)

Do this procedure one time on each computer.

1. Open a terminal.
2. Go to the sentinel source folder:

   ```bash
   cd ~/jan25/ollama-sentinel
   ```

3. Install the sentinel:

   ```bash
   pip install -e .
   ```

4. Make sure that the install worked:

   ```bash
   ollama-sentinel --version
   ```

---

## 3. Start Ollama

The sentinel cannot review code when Ollama is not running. Do this procedure before you use the sentinel.

1. Start the Ollama server. You can also open the Ollama app instead.

   ```bash
   ollama serve
   ```

2. Download a review model if you do not have one. This example uses a small model:

   ```bash
   ollama pull gemma3:4b
   ```

3. Download the search model. The sentinel uses this model to find related old findings. The download is approximately 2.5 GB.

   ```bash
   ollama pull qwen3-embedding:4b
   ```

4. Make sure that Ollama has your models:

   ```bash
   ollama list
   ```

If you do not want to download the search model, see [Section 13](#13-change-the-settings).

---

## 4. Set up a project (one time for each project)

CAUTION: The sentinel reads `ollama-sentinel.yaml` from the folder where you type the command. Always type sentinel commands in the project folder.

1. Make sure that Ollama is running (see [Section 3](#3-start-ollama)).
2. Go to your project folder:

   ```bash
   cd ~/path/to/your/project
   ```

3. Make the config file:

   ```bash
   ollama-sentinel init
   ```

The `init` command finds a review model that Ollama already has. It writes the name of that model into the config file. If a config file already exists, `init` asks before it replaces it.

---

## 5. Use watch mode

In watch mode, the sentinel reviews each file when you save it.

1. Go to your project folder.
2. Start watch mode:

   ```bash
   ollama-sentinel run
   ```

3. Edit a file and save it.
4. Wait a few seconds. The review shows in the terminal.
5. To stop watch mode, push `Ctrl-C`.

The sentinel saves each review in the folder `.ollama_reviews/`. To see the findings, open the Control Center in a second terminal (see [Section 8](#8-use-the-control-center)).

---

## 6. Use check mode

Check mode reviews the files that you changed in git. It does not watch for saves.

1. Go to your project folder.
2. Review all changed files and all new files:

   ```bash
   ollama-sentinel check
   ```

3. To review only the files that you staged with `git add`, add `--staged`:

   ```bash
   ollama-sentinel check --staged
   ```

4. To fail when a finding is `high` or worse, add `--fail-on high`:

   ```bash
   ollama-sentinel check --staged --fail-on high
   ```

The `check` command shows a table of open findings in the changed files. It stops with exit code 1 in two conditions:

- A finding has the `--fail-on` severity or worse.
- The model could not review a file. The message is "Review failed for N file(s)". Usually, Ollama is not running.

---

## 7. Review one file

1. Go to your project folder.
2. Review the file:

   ```bash
   ollama-sentinel review src/app.py
   ```

3. To use a different model role from the config file, add `-m` and the role name:

   ```bash
   ollama-sentinel review src/app.py -m security
   ```

A model role is a named model in the config file. Each config file has a role with the name `default`.

---

## 8. Use the Control Center

The Control Center shows your findings in the terminal. You can close and fix findings from it.

1. Go to your project folder.
2. Open the Control Center:

   ```bash
   ollama-sentinel dashboard
   ```

3. To show findings that the model reported only one time, add `-n 1`:

   ```bash
   ollama-sentinel dashboard -n 1
   ```

The Patterns panel shows only findings that the model reported 2 or more times. That is the default. Use `-n 1` to show all open findings.

The Control Center has four panels:

| Panel | What it shows |
|---|---|
| Overview | The number of open findings for each severity, and a suggested next step. |
| Recent Reviews | The last reviews, with the time of each review. |
| Patterns | The open findings. You select a finding here to close it or fix it. |
| Guardrails | Your active guardrails. |

### Keys

| Key | What it does |
|---|---|
| `Tab` | Move to the next panel. |
| `Shift-Tab` | Move to the previous panel. |
| `j` or `↓` | Move down one line. |
| `k` or `↑` | Move up one line. |
| `Enter` | Show the full text of the selected item. |
| `Esc` | Go back. |
| `/` | Filter the Patterns panel. Type a severity or a category, then push `Enter`. |
| `r` | Resolve the selected finding. Use this when you fixed the problem. |
| `d` | Dismiss the selected finding. Use this when the finding is wrong. |
| `c` | Confirm the selected finding. Use this when the problem is real. The finding stays open. |
| `f` | Ask the model for a fix. The Control Center shows the change. |
| `y` | Write the fix into the file. |
| `n` | Cancel the fix. |
| `q` | Quit. |

The keys `r`, `d`, `c`, and `f` work only in the Patterns panel. When the Control Center opens, the Overview panel is selected. Push `Tab` two times to go to the Patterns panel.

### Fix a finding in the Control Center

CAUTION: The `y` key writes to your source file. Read the change before you push `y`. The sentinel does not commit the change.

1. Push `Tab` two times to go to the Patterns panel.
2. Push `j` or `k` to select the finding.
3. Push `f`. Wait for the model to make the fix.
4. Read the change.
5. Push `y` to write the fix, or push `n` to cancel.

When you push `y`, the sentinel also resolves the finding.

---

## 9. Work with findings from the command line

You can do all Control Center tasks with commands. Each command uses the finding ID.

### Get the finding IDs

1. Show the open findings:

   ```bash
   ollama-sentinel findings
   ```

2. To show only one severity, add `--severity`:

   ```bash
   ollama-sentinel findings --severity high
   ```

3. To show only one file, add `--file` and part of the file name:

   ```bash
   ollama-sentinel findings --file auth
   ```

### Close a finding

| Task | Command |
|---|---|
| You fixed the problem. | `ollama-sentinel resolve 42` |
| The finding is wrong. | `ollama-sentinel dismiss 42` |

### Confirm a finding

Use `confirm` when the problem is real but you did not fix it yet. The finding stays open. You can add a note:

```bash
ollama-sentinel confirm 42 -n "Saw this crash in production"
```

### Fix a finding with the model

CAUTION: This command writes to your source file. Read the change before you type `y`. The sentinel does not commit the change.

1. Ask the model for a fix:

   ```bash
   ollama-sentinel fix 42
   ```

2. Read the change.
3. Type `y` to write the fix, or type `n` to cancel.

When you write the fix, the sentinel resolves the finding. The sentinel changes only the lines of the finding. If the sentinel cannot find those lines, it stops and does not write.

### Close old findings

The `prune` command closes findings whose code is not in the file now.

1. Show the findings that `prune` will close:

   ```bash
   ollama-sentinel prune
   ```

2. Read the list.
3. Type `y` to close them, or type `n` to stop.

In watch mode, the sentinel also does this for each file that you save.

---

## 10. Block bad commits

The pre-commit hook runs `check --staged` before each commit. If a finding is too severe, git stops the commit.

WARNING: When Ollama is not running, the hook stops every commit. To commit without the hook, use `git commit --no-verify`.

CAUTION: Git runs the hook in the top folder of the repository. Put `ollama-sentinel.yaml` in that folder.

1. Go to your project folder.
2. Install the hooks:

   ```bash
   ollama-sentinel install-hooks --pre-commit
   ```

3. To use a different severity limit, add `--fail-on`. The default is `high`.

   ```bash
   ollama-sentinel install-hooks --pre-commit --fail-on critical
   ```

The command also installs a post-commit hook. That hook links each commit to the open findings in the files that you changed. If a hook already exists, the sentinel does not replace it.

---

## 11. Diagnose an error

The `triage` command sends error output to the model. The model tells you the cause and a fix. The sentinel finds the files in the error and sends them to the model too.

1. Save the error output to a file, or send it directly with a pipe:

   ```bash
   pytest 2>&1 | ollama-sentinel triage
   ```

2. To read the error from a file, give the file name:

   ```bash
   ollama-sentinel triage error.log
   ```

3. To save the answer, add `-o` and a file name:

   ```bash
   ollama-sentinel triage error.log -o answer.md
   ```

4. To send one more source file to the model, add `--context`:

   ```bash
   ollama-sentinel triage error.log --context src/app.py
   ```

---

## 12. Write rules (guardrails)

A guardrail is a rule in plain English. The model checks each review against your active guardrails.

### Add a guardrail

1. Give the guardrail a short name and the rule text:

   ```bash
   ollama-sentinel guardrail add no-eval -a "Never call eval on user input."
   ```

2. To use the guardrail only for some files, add `--path`:

   ```bash
   ollama-sentinel guardrail add no-eval -a "Never call eval on user input." --path "src/*.py"
   ```

The guardrail is active immediately.

### Manage guardrails

| Task | Command |
|---|---|
| Show the active guardrails. | `ollama-sentinel guardrail list` |
| Show all guardrails. | `ollama-sentinel guardrail list --all` |
| Change the rule text. | `ollama-sentinel guardrail edit 3 -a "New rule text."` |
| Stop a guardrail for some time. | `ollama-sentinel guardrail disable 3` |
| Start a stopped guardrail again. | `ollama-sentinel guardrail enable 3` |
| Remove a guardrail permanently. | `ollama-sentinel guardrail dismiss 3` |

### Accept rules that the sentinel suggests

The sentinel can suggest guardrails from problems that occur frequently. A suggestion needs 3 or more confirmed findings of the same type. A confirmed finding is one that you confirmed, that a failed test matched, or that a fix commit closed.

1. Show the suggestions:

   ```bash
   ollama-sentinel guardrail candidates
   ```

2. To accept suggestion number 1, type:

   ```bash
   ollama-sentinel guardrail promote 1
   ```

3. To refuse suggestion number 1, type:

   ```bash
   ollama-sentinel guardrail reject 1
   ```

A refused suggestion does not come back.

---

## 13. Change the settings

The settings are in `ollama-sentinel.yaml` in your project folder. Edit the file with a text editor. Restart watch mode after you change the file.

| Setting | What it does |
|---|---|
| `ollama.models.default.name` | The model that does the reviews. |
| `watch.ignore_patterns` | Files that the sentinel does not review. |
| `memory.semantic_recall` | Set to `false` if you do not have the search model (`qwen3-embedding:4b`). |
| `processing.git_diff_mode` | Set to `true` to review only the changed lines, not the full file. |
| `output.console_output` | Set to `false` to stop reviews in the terminal. |

To add a model role, add a new entry under `ollama.models`. Give it a `name` and a `system_prompt`. Then use the role with `-m` (see [Section 7](#7-review-one-file)).

---

## 14. Other commands

| Command | What it does |
|---|---|
| `ollama-sentinel report` | Shows the findings that the model reported 2 or more times. |
| `ollama-sentinel incidents` | Shows the confirmed findings from the last 30 days. |
| `ollama-sentinel surface` | Writes the open findings to `.ollama_reviews/findings.sarif`. Editors and CI tools can read this file. |
| `ollama-sentinel repair` | Corrects the severity of old findings in the database. It shows the changes first. |
| `ollama-sentinel research "QUESTION"` | Researches a library question with web search and your code. |

The `research` command needs more software and an OpenAI key:

```bash
pip install -e ".[research]"
playwright install chromium
export OPENAI_API_KEY="your-key"
```

To see all options for a command, add `--help`. For example: `ollama-sentinel check --help`.

---

## 15. Problems and solutions

| Problem | Cause | Solution |
|---|---|---|
| The command says "Configuration file not found". | There is no config file in this folder. | Go to your project folder, or run `ollama-sentinel init`. |
| `check` says "Review failed". | Ollama is not running. | Start Ollama (see [Section 3](#3-start-ollama)). |
| Git does not let you commit. | The pre-commit hook found a severe finding, or Ollama is not running. | Fix the finding, or start Ollama. To skip the hook one time, use `git commit --no-verify`. |
| The log says "Semantic embedding unavailable". | The search model is not downloaded, or Ollama is not running. | Run `ollama pull qwen3-embedding:4b`, or set `memory.semantic_recall: false`. |
| The Patterns panel is empty, but `findings` shows findings. | The panel shows only findings seen 2 or more times. | Open the Control Center with `ollama-sentinel dashboard -n 1`. |
| `fix` stops and does not write. | The code of the finding moved or changed. | Run `ollama-sentinel prune` to close old findings. |
| A review is slow. | The model is large, or Ollama loads the model for the first time. | Wait for the first review. Use a smaller model if all reviews are slow. |
