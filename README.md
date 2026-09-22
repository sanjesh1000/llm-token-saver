# Distill

A lightweight, zero-dependency Python CLI that distills your codebase into a single, compressed, LLM-friendly context file — so you spend fewer tokens (and less money/context window) feeding your project to **Claude Code**, **GitHub Copilot Chat**, **Cursor**, **Kiro**, or any other AI assistant.

It strips comments, collapses whitespace, optionally erases TypeScript type annotations, skips binaries/lockfiles/build output, and wraps everything in clean `<file path="...">` tags that LLMs parse natively — plus it tells you exactly how many tokens you saved.

## Contents

- [Why Distill](#why-distill)
- [How It Works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration--distilljson)
- [Usage](#usage)
- [Example Output](#example-output)
- [Editor & Workflow Integration](#editor--workflow-integration)
- [Notes, Limits & Best Practices](#notes-limits--best-practices)

---

## Why Distill

When you paste your codebase into Claude Code or Copilot Chat, you're often sending raw files full of comments, blank lines, lockfiles, build artifacts, and huge generated files — all of which cost tokens (money + context window space) without adding much value to the AI's understanding of your logic.

Distill scans your project once, strips out the noise, and hands the AI a compact, pre-cleaned snapshot instead.

## How It Works

1. **Reads your config** (`.distill.json`) — looks in your project root for this file. If it's not there, safe built-in defaults are used. This tells Distill what to skip, what to shrink, and what to prioritize.
2. **Walks your project folder** — scans every file/subfolder but immediately skips anything matching `excludePatterns`, so it never even opens `node_modules/`, `.git/`, `dist/`, images, lockfiles, etc. This keeps it fast even on huge repos.
3. **Filters further**
   - Skips files bigger than `maxFileSizeBytes` (stops one giant generated JSON file from eating your whole budget).
   - Only keeps extensions listed in `includeExtensions` (so it doesn't accidentally grab random binary or config junk).
4. **Orders the files** — anything in your `priorityFiles` list (like `architecture.md` or `index.ts`) gets moved to the front of the bundle, so if the AI's context window gets truncated, the most important files are still in.
5. **Compresses each file's content**, depending on your `compressMode` settings:
   - **`stripComments`** — removes `//`, `/* */`, `#` comments and docstrings (smart enough to not eat `http://` URLs or `#` inside strings).
   - **`stripWhitespace`** — collapses multiple blank lines and trims trailing spaces.
   - **`stripTypes`** — (TypeScript only) strips type annotations, interfaces, and generics, since an AI reading logic often doesn't need the type system, just the code paths.
6. **Bundles everything into one file** — all surviving, compressed files are wrapped in `<file path="...">` tags and written to `context-bundle.xml` (or `.json`). This format is cheap for LLMs to parse — they don't have to guess where one file ends and another begins.
7. **Reports what it saved** — writes `token-savings-report.md` showing raw vs. compressed character/token counts, in total and per-file, so you can see exactly how much you cut.

## Requirements

- Python 3.8+
- No required third-party packages (pure standard library)
- **Optional:** `pip install tiktoken` for exact token counts (otherwise falls back to a `chars / 4` estimate, accurate to within a few percent for most code/English text)

## Installation

Just drop `distill.py` into your project (or a shared tools folder / dotfiles repo). No build step, no `npm install`, no virtualenv required.

```bash
# Option A: keep it in the repo
cp distill.py ./scripts/distill.py

# Option B: keep it globally and alias it
mkdir -p ~/bin && cp distill.py ~/bin/distill.py
chmod +x ~/bin/distill.py
echo 'alias distill="python3 ~/bin/distill.py"' >> ~/.zshrc   # or ~/.bashrc
```

Then run it from any project root:

```bash
distill
# or
python3 distill.py
```

## Configuration — `.distill.json`

Drop a `.distill.json` (or `.distillrc`, same format) in your project root. If none is found, sensible defaults are used automatically. A ready-to-copy sample lives at [examples/.distill.json](/examples/.distill.json).

```json
{
  "excludePatterns": [
    "**/node_modules/**",
    "**/.git/**",
    "**/dist/**",
    "**/build/**",
    "**/.venv/**",
    "**/*.lock",
    "**/*.png", "**/*.jpg", "**/*.svg"
  ],
  "compressMode": {
    "stripComments": true,
    "stripWhitespace": true,
    "stripTypes": false
  },
  "maxFileSizeBytes": 150000,
  "priorityFiles": ["architecture.md", "README.md", "src/index.ts"],
  "includeExtensions": [".js", ".ts", ".py", ".json", ".md"],
  "outputFile": "context-bundle.xml",
  "reportFile": "token-savings-report.md"
}
```

### Field reference

| Field | Type | Purpose |
|---|---|---|
| `excludePatterns` | string[] (glob) | Files/dirs to skip entirely. Supports `**` wildcards. |
| `compressMode.stripComments` | bool | Removes `//`, `/* */`, `#` comments and docstrings, string-literal-aware. |
| `compressMode.stripWhitespace` | bool | Collapses multiple blank lines, trims trailing whitespace. |
| `compressMode.stripTypes` | bool | **TypeScript only.** Best-effort removal of type annotations, interfaces, and generics via regex (not a full compiler pass — don't use the output as compilable code, only as LLM context). |
| `maxFileSizeBytes` | int | Files larger than this are skipped, to stop one huge generated file from blowing the budget. |
| `priorityFiles` | string[] | Filenames or relative paths bundled **first**, in the order given (great for architecture docs / entrypoints you want the model to read before anything else). |
| `includeExtensions` | string[] | Allow-list of file extensions to include. Leave out an extension and it's ignored even if not excluded. |
| `outputFile` | string | Default bundle filename (`.xml` or `.json`, inferred from extension unless `--format` overrides it). |
| `reportFile` | string | Default savings-report filename. |

> Distill **always** excludes its own generated files (`.distill.json`, `context-bundle.*`, `token-savings-report.md`) even if your `excludePatterns` doesn't mention them — so re-running it never bundles its own previous output.

## Usage

```bash
# Run with defaults / local .distill.json
python3 distill.py

# Scan a different directory
python3 distill.py --root ./apps/backend

# Force JSON bundle instead of XML
python3 distill.py --format json

# Custom output/report paths
python3 distill.py --output /tmp/context.xml --report /tmp/savings.md

# Preview what would happen without writing the bundle file
python3 distill.py --dry-run

# Use a config file that isn't in the project root
python3 distill.py --config ./configs/backend.distill.json
```

### CLI flags

| Flag | Description |
|---|---|
| `--root PATH` | Project root to scan (default: current directory) |
| `--config PATH` | Explicit config file path |
| `--format {xml,json}` | Output bundle format |
| `--output PATH` | Bundle output file path |
| `--report PATH` | Savings report output path |
| `--dry-run` | Scan + report only, skip writing the bundle |

## Example Output

Running Distill produces two files. Full samples are checked in under [examples/](/examples):

- [examples/context-bundle.xml](/examples/context-bundle.xml) — the file you paste into Claude Code / Copilot Chat
- [examples/token-savings-report.md](/examples/token-savings-report.md) — a summary of what was saved

**`context-bundle.xml`** (excerpt):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<codebase>
  <file path="architecture.md">
# Architecture
This describes the system.
  </file>
  <file path="src/index.ts">
import { foo } from "./foo";
function add(a, b) {
    return a + b;
}
  </file>
</codebase>
```

**`token-savings-report.md`** (excerpt):

```markdown
# Token Savings Report

- Generated: 2026-09-18 21:23:59
- Token counting method: heuristic (chars / 4, approximate)
- Files included: 4
- Output format: xml

## Totals
| Metric | Raw | Compressed | Saved |
|---|---:|---:|---:|
| Characters | 745 | 393 | 47.2% |
| Tokens (est.) | 184 | 97 | 47.3% |

## Per-file breakdown
| File | Raw tokens | Compressed tokens | Saved |
|---|---:|---:|---:|
| src/index.ts | 113 | 43 | 62% |
| src/main.py | 52 | 35 | 33% |
```

## Editor & Workflow Integration

### Claude Code (terminal-based)

1. Run `python3 distill.py` in your project root before starting a session.
2. Reference the bundle directly in your prompt:
   ```
   claude "Read context-bundle.xml, then refactor the auth module to use JWT."
   ```
3. **Optional — make it automatic:** add a `CLAUDE.md` file in your project root telling Claude Code to always check the bundle first:
   ```markdown
   # Project Instructions
   Before making changes, read context-bundle.xml for a compact overview
   of the codebase. Regenerate it with `python3 distill.py` if it looks stale.
   ```
   Claude Code reads `CLAUDE.md` automatically at the start of every session in that directory.

### GitHub Copilot Chat (VS Code)

1. Run `python3 distill.py` — generates `context-bundle.xml`.
2. Open Copilot Chat in VS Code (sidebar icon or `Ctrl/Cmd+Shift+I`).
3. Type `#file:context-bundle.xml` before your question, e.g.:
   ```
   #file:context-bundle.xml Explain how the payment flow works
   ```
   This is much cheaper than `#codebase` scanning everything raw, especially in large repos.
4. **Optional — make it automatic:** add a `.github/copilot-instructions.md` file:
   ```markdown
   When you need broader project context, check context-bundle.xml in the
   project root — it's a compressed snapshot of the codebase.
   ```
   Copilot picks this file up automatically as repo-level custom instructions.

### Kiro

1. Run `python3 distill.py`.
2. Create the steering folder if it doesn't exist: `mkdir -p .kiro/steering`.
3. Either copy the bundle in directly, or (better, since steering files are meant to be short markdown, not a huge dump) create a small pointer file `.kiro/steering/context-bundle.md`:
   ```markdown
   # Codebase Context
   A compressed, up-to-date snapshot of this project lives in
   context-bundle.xml at the project root. Regenerate it with
   `python3 distill.py` whenever the codebase changes significantly.
   ```
4. Kiro automatically loads everything in `.kiro/steering/` into every new session in that workspace — no extra step needed per-session.

### Cursor

Cursor's current approach (as of 2026) is `.cursor/rules/*.mdc` files. The older single `.cursorrules` file is deprecated but still works on older Cursor versions.

1. Run `python3 distill.py` — generates `context-bundle.xml`.
2. Create the rules directory: `mkdir -p .cursor/rules`.
3. Create a rule file, e.g. `.cursor/rules/context-bundle.mdc`, with YAML frontmatter + instructions:
   ```markdown
   ---
   description: "Compressed codebase context bundle"
   alwaysApply: true
   globs:
   ---
   # Project Context
   A compressed, up-to-date snapshot of this codebase lives in
   context-bundle.xml at the project root. Reference it for broad
   codebase understanding instead of scanning every file individually.
   Regenerate it with `python3 distill.py` if it looks stale.
   ```
4. Cursor picks up `.mdc` files in `.cursor/rules/` automatically — no restart needed, and `alwaysApply: true` means it's injected into every chat/agent session in that project.
5. For a one-off task instead of always-on context, skip the rule file and just `@file` it directly in chat:
   ```
   @context-bundle.xml Refactor the auth module to use JWT.
   ```

### One-click VS Code Task

Add this to `.vscode/tasks.json` so you can run it via **Cmd/Ctrl+Shift+P → "Run Task"**:

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Distill: Build Context Bundle",
      "type": "shell",
      "command": "python3 distill.py",
      "problemMatcher": [],
      "presentation": { "reveal": "always", "panel": "shared" }
    }
  ]
}
```

### Optional pre-commit / CI hygiene

If you want the bundle regenerated automatically before pushing (e.g., to keep an always-fresh context file in the repo for teammates), add to `.git/hooks/pre-push` or a Husky hook:

```bash
python3 distill.py --dry-run || true   # sanity check, doesn't block push
```

> A tip that applies to all editors: re-run `python3 distill.py` after significant changes so the bundle doesn't go stale — none of these tools regenerate it for you automatically.

## Notes, Limits & Best Practices

- **`stripComments` is string-literal-aware** for the common cases (won't eat `"http://..."` or `# not-a-comment` inside quotes) but is a fast heuristic, not a full language parser — spot-check output on unusual syntax.
- **`stripTypes` is best-effort** (regex-based) and intended purely to shrink LLM context, not to produce valid, re-compilable TypeScript. Keep it `false` if you ever intend to feed the bundle back as source.
- Large monorepos: use `--root` per-package and generate multiple smaller bundles rather than one giant one — most models perform better with focused, relevant context than a maximal dump.
- Re-running the tool is always safe — it never bundles its own previous `context-bundle.*` or `token-savings-report.md` output.
- Install `tiktoken` (`pip install tiktoken`) for exact token counts matching OpenAI/Anthropic tokenizers; without it, the tool uses a `chars / 4` estimate that's typically within ~10% of the true count for code and English prose.
