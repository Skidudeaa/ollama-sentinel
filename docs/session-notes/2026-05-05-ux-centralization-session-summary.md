# UX Centralization Session Summary

Date: 2026-05-05  
Repo: `ollama-sentinel`  
Topic: Making the user experience more centralized and visually intuitive

## 1. Original question
The goal was to answer: **how can we make the user experience more centralized and visually intuitive?**

The discussion focused on the current Ollama Sentinel UX and how to evolve it from a set of adjacent CLI capabilities into a single coherent product experience.

---

## 2. Repo-informed observations

Based on inspection of the repository, the current experience is split across several surfaces:

- `ollama_sentinel/cli.py`
  - exposes `run`, `review`, `init`, `report`, `triage`, and `dashboard`
- `ollama_sentinel/dashboard.py`
  - renders a read-only live Rich dashboard with two primary panes:
    - recent reviews
    - recurring violations
- `research_agent/cli/interface.py`
  - provides a separate interactive CLI for research
  - includes its own welcome flow, help, history, configuration display, progress, and results formatting

### Core UX issue identified
The product is **capability-rich but experience-fragmented**.

A user currently has to mentally stitch together:
- watcher operations
- one-off reviews
- recurring issue reporting
- triage workflows
- a separate research interface

That means the UX is organized more around implementation commands than around user goals.

---

## 3. High-level recommendation given
The strongest recommendation was to make Sentinel feel like **one product with one home**.

### Central thesis
Instead of users thinking:
- run watcher
- maybe run dashboard
- maybe run report
- maybe use research separately

They should think:
- **open Sentinel**
- see what is happening
- understand what needs attention
- choose the next action from one central surface

### Primary recommendation
Promote the dashboard into a **real control center** rather than a side utility.

---

## 4. Main UX recommendations that were proposed

### 4.1 Make the dashboard the default home
The current dashboard is useful but too passive. It should become the main product entrypoint.

Recommended long-term behavior:

```bash
ollama-sentinel
```

with no subcommand opening the main control center.

### 4.2 Use one top-level mental model
A simpler information architecture was proposed:

- **Overview**
- **Reviews**
- **Patterns**
- **Triage**
- **Research**
- **Settings**

This structure is more intuitive than a command-based mental model.

### 4.3 Organize around workflows instead of commands
The interface should help users answer:
- What changed?
- What keeps going wrong?
- Why did this fail?
- Is this upgrade safe?
- What should I do next?

---

## 5. Concrete UX spec that was produced
A repo-specific UX specification was drafted.

## 5.1 Product goal
Transform Ollama Sentinel from a collection of useful commands into a **single coherent product experience** centered on one primary interface.

## 5.2 Product principle
**One home, many workflows.**

Users should not have to remember whether they need `run`, `dashboard`, `report`, `triage`, or a separate research CLI.

## 5.3 Information architecture
Recommended top-level sections:
1. Overview
2. Reviews
3. Patterns
4. Triage
5. Research
6. Settings

## 5.4 Primary interface: Sentinel Control Center
Suggested layout:
- **Header**
  - project name / directory
  - watcher state
  - model state
  - last update time
  - active config
  - db status
- **Left navigation**
  - Overview / Reviews / Patterns / Triage / Research / Settings
- **Main content area**
  - active screen content
- **Right detail rail**
  - selected item details, quick actions, contextual explanation, filters
- **Footer**
  - keyboard hints and status messages

---

## 6. Screen-by-screen UX recommendations

### 6.1 Overview screen
Purpose: provide a high-signal home screen.

Suggested contents:
- watcher status
- review count in recent time windows
- open recurring pattern count
- latest triage summary
- latest research summary
- recent activity feed
- suggested next action
- quick actions

### 6.2 Reviews screen
Purpose: show recent file reviews in a scan-friendly way.

Suggested structure:
- main list with file, time, finding count, highest severity
- detail pane with summary and key findings
- filters by severity, recency, unresolved status, directory

### 6.3 Patterns screen
Purpose: elevate recurring violations into a first-class concept.

Recommendation: rename “Recurring Violations” to **Patterns**.

Suggested structure:
- occurrence count
- severity
- category
- last seen
- affected files count
- trend direction
- suggested remediation framing

### 6.4 Triage screen
Purpose: make failure diagnosis part of the same product surface.

Suggested structure:
- paste log/output or load a file
- recent triage history
- likely root cause
- referenced files
- suggested next step

### 6.5 Research screen
Purpose: unify research-agent functionality under Sentinel.

Suggested structure:
- one prompt box
- history
- answer summary
- confidence
- ranked affected files
- supporting sources
- suggested next action

### 6.6 Settings screen
Purpose: show environment and system state in a trustworthy way.

Suggested structure:
- config path
- watched directory
- output directory
- db path
- model roles
- validation warnings
- explicit system health explanations

---

## 7. Visual design recommendations

### 7.1 Stronger information hierarchy
The existing dashboard is heavily row-based. The recommendation was to increase scanability with:
- status chips
- top-level counts
- clear severity semantics
- summary blocks
- detail panes

### 7.2 Consistent semantic colors
Recommended color semantics:
- red = critical / urgent / failing
- yellow = warning / medium / needs attention
- green = healthy / complete / stable
- blue/cyan = informational / active / navigation
- dim/gray = metadata / inactive / secondary

### 7.3 Cards vs tables
Recommendation:
- use cards for high-level summaries and quick actions
- use tables/lists for detailed histories and item collections
- use detail panes for full explanations

### 7.4 Progressive disclosure
Do not cram all detail into list rows. Show summary first; full detail in a side pane or expanded view.

---

## 8. Interaction recommendations
Recommended future interaction model:
- `Tab` / `Shift-Tab` to move between sections
- arrow keys or `j` / `k` for list navigation
- `Enter` to inspect/open
- `/` to search/filter
- `?` for keyboard help
- `q` to quit
- future quick actions for review, triage, patterns, and research

The advice was to eventually make the TUI interactive, but **not to do that first**.

---

## 9. Recommendation about what to do next
When asked whether to produce:
1. a concrete UX spec, or
2. a direct patch proposal,

the recommendation was:
- **do the UX spec first**
- then create a file-by-file developer implementation plan
- then do a Phase 1 patch

Reasoning:
- command behavior, naming, layout, and screen structure all interact
- patching too early risks locking in the wrong architecture

---

## 10. File-by-file developer implementation plan that was produced
A detailed implementation plan was recommended, organized into phases.

## 10.1 Phase 1 — Control Center v1
Goal:
- improve the existing dashboard into a clearer home screen
- keep it read-only
- avoid risky architecture changes

Target deliverables:
- Overview/home screen
- renamed Patterns section
- better header status badges
- better footer/help
- one detail/summary area
- improved hierarchy

### Files recommended for Phase 1
- `ollama_sentinel/dashboard.py`
- `ollama_sentinel/cli.py`
- `README.md`
- `docs/GUIDE.md`

## 10.2 Phase 2 — UX unification
Goal:
- align naming and entrypoints
- reduce split-brain UX between Sentinel and Research

Target files:
- `ollama_sentinel/cli.py`
- `research_agent/cli/interface.py`
- `research_agent/main.py`
- possibly shared rendering helpers

## 10.3 Phase 3 — Interactive TUI
Goal:
- make the interface navigable, not just live

Likely additions:
- selection state
- keyboard navigation
- screen switching
- search/filter
- richer inspector/details

Potential supporting files:
- `ollama_sentinel/ui_state.py`
- `ollama_sentinel/ui_components.py`

---

## 11. Detailed file-specific recommendations

### `ollama_sentinel/dashboard.py`
This was identified as the most important file for the UX shift.

Recommended changes:
- introduce a lightweight screen model
- refactor rendering into composable sections
- add an Overview section
- rename recurring violations to “Patterns”
- add a right-side inspector/detail panel
- improve the footer
- strengthen status semantics

### `ollama_sentinel/cli.py`
Recommended changes:
- add a future-friendly central entrypoint
- possibly add `ui` as an alias to `dashboard`
- align command descriptions with product vocabulary
- reduce conceptual duplication between `report` and dashboard language
- prepare alias strategy for later command unification

### `README.md`
Recommended changes:
- shift from command-centric teaching to workflow-centric teaching
- promote the Control Center / dashboard as the product home
- unify terminology around Reviews / Patterns / Triage / Research / Settings
- add a “daily workflow” section

### `docs/GUIDE.md`
Recommended changes:
- add a Control Center section
- explain workflows instead of isolated commands
- document the centralized usage pattern

### `docs/VISION.md`
Recommended changes:
- add a short note about the product surface being one local-first companion with one primary control surface
- align visible product terminology where appropriate

### `research_agent/cli/interface.py`
Recommended changes:
- do not merge immediately
- audit reusable rendering ideas
- gradually align visible language
- plan integration later, not in Phase 1

### Possible new files
- `ollama_sentinel/ui_state.py`
- `ollama_sentinel/ui_components.py`

These were recommended as optional future support files if the TUI grows in complexity.

---

## 12. Specific recommendation when asked what to do first
When asked which output I recommended next, the answer was:
- **developer implementation plan with file-by-file changes**
- then a small Phase 1 patch proposal
- then implementation

The rationale given was that this was safer and better aligned with the repo’s current structure.

---

## 13. Prioritized implementation order that was recommended
If implementation begins, the recommended order was:

1. `ollama_sentinel/dashboard.py`
2. `ollama_sentinel/cli.py`
3. `README.md`
4. `docs/GUIDE.md`
5. later: `research_agent/cli/interface.py`

---

## 14. Recommended success criteria
Phase 1 would be considered successful if:
- users can treat the dashboard as the main home
- the interface answers “what’s happening?” at a glance
- “Patterns” feels clearer than “Recurring Violations”
- the product feels more cohesive without changing core behavior
- docs guide users into one main workflow rather than scattered commands

Longer-term success would mean a new user can:
1. launch Sentinel without memorizing commands
2. immediately understand system status
3. find recent reviews and recurring patterns in one place
4. access triage and research without changing mental models
5. know the next recommended action without reading docs first

---

## 15. Final practical recommendation from the session
If work resumes later, the recommended next step is:

### Best next actionable step
Create a **Phase 1 task list with exact edits per file**, focused on:
- `ollama_sentinel/dashboard.py`
- `ollama_sentinel/cli.py`
- minimal doc updates

This keeps scope controlled while producing the biggest UX improvement quickly.

### What was explicitly not recommended yet
- do not merge research and sentinel rendering immediately
- do not remove or rename commands in a breaking way yet
- do not jump to a web app before proving the information architecture in the TUI
- do not overbuild interactivity before the layout and naming are right

---

## 16. Short summary
This session concluded that the strongest UX improvement for Ollama Sentinel is to turn it into a **single coherent control-center experience**.

The key ideas were:
- one home
- workflow-first IA
- consistent naming
- “Patterns” as the user-facing concept instead of “Recurring Violations”
- a staged implementation starting with dashboard improvements rather than a rewrite

