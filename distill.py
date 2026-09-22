#!/usr/bin/env python3
"""
distill.py — Context Bundler & Token Optimizer for Claude Code / GitHub Copilot

Aggregates a codebase into a single, minified, LLM-friendly context bundle
(XML or JSON) so you can hand a whole project to an AI assistant without
burning tokens on node_modules, whitespace, comments, or duplicate boilerplate.

Usage:
    python distill.py                     # run with defaults / .distill.json
    python distill.py --root ./my-project
    python distill.py --format json
    python distill.py --dry-run
    python distill.py --config custom.json

Zero required dependencies (stdlib only). Optional: `pip install tiktoken`
for exact token counts instead of the char/4 estimate.

Author: Claude (Anthropic) — generated tool, MIT-licensed for the user's project.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------
# Optional exact tokenizer (falls back to a char/4 heuristic if unavailable)
# --------------------------------------------------------------------------
try:
    import tiktoken  # type: ignore

    _ENC = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        if not text:
            return 0
        return len(_ENC.encode(text, disallowed_special=()))

    TOKEN_METHOD = "tiktoken (cl100k_base, exact)"
except ImportError:
    def count_tokens(text: str) -> int:
        # Reasonable heuristic for English/code: ~4 chars per token.
        return max(1, len(text) // 4) if text else 0

    TOKEN_METHOD = "heuristic (chars / 4, approximate)"


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "excludePatterns": [
        "**/node_modules/**",
        "**/.git/**",
        "**/.hg/**",
        "**/.svn/**",
        "**/dist/**",
        "**/build/**",
        "**/out/**",
        "**/.next/**",
        "**/.venv/**",
        "**/venv/**",
        "**/__pycache__/**",
        "**/.pytest_cache/**",
        "**/.mypy_cache/**",
        "**/coverage/**",
        "**/.cache/**",
        "**/*.min.js",
        "**/*.min.css",
        "**/*.map",
        "**/*.lock",
        "**/package-lock.json",
        "**/yarn.lock",
        "**/pnpm-lock.yaml",
        "**/*.png", "**/*.jpg", "**/*.jpeg", "**/*.gif", "**/*.webp",
        "**/*.ico", "**/*.svg", "**/*.pdf", "**/*.zip", "**/*.tar",
        "**/*.gz", "**/*.woff", "**/*.woff2", "**/*.ttf", "**/*.eot",
        "**/*.mp4", "**/*.mov", "**/*.mp3", "**/*.wav",
        "**/*.db", "**/*.sqlite", "**/*.sqlite3",
        "**/.DS_Store",
        "**/context-bundle.*",
        "**/token-savings-report.md",
        "**/.distill.json",
        "**/.distillrc",
    ],
    "compressMode": {
        "stripComments": True,
        "stripWhitespace": True,
        "stripTypes": False,
    },
    "maxFileSizeBytes": 200_000,  # ~200 KB per file ceiling
    "priorityFiles": [
        "architecture.md",
        "README.md",
        "index.ts",
        "index.js",
        "main.py",
        "schema.prisma",
    ],
    "includeExtensions": [
        ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
        ".py", ".go", ".rs", ".java", ".kt", ".c", ".h", ".cpp", ".hpp",
        ".cs", ".rb", ".php", ".swift",
        ".json", ".yaml", ".yml", ".toml",
        ".md", ".mdx", ".txt",
        ".html", ".css", ".scss",
        ".sql", ".graphql", ".prisma",
        ".sh", ".bash",
    ],
    "outputFile": "context-bundle.xml",
    "reportFile": "token-savings-report.md",
}

CONFIG_FILENAMES = [".distill.json", ".distillrc"]

# These are always excluded, even if a user's config fully replaces
# excludePatterns — otherwise a bundle can accidentally ingest its own
# previous output on the next run.
ALWAYS_EXCLUDE = [
    "**/.distill.json",
    "**/.distillrc",
    "**/context-bundle.xml",
    "**/context-bundle.json",
    "**/token-savings-report.md",
]


def load_config(root: Path, explicit_path: Optional[str]) -> dict:
    """Load and merge user config over defaults. Never raises on bad config;
    warns and falls back to defaults instead, so the tool always runs."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy

    candidate: Optional[Path] = None
    if explicit_path:
        candidate = Path(explicit_path)
    else:
        for name in CONFIG_FILENAMES:
            p = root / name
            if p.exists():
                candidate = p
                break

    if candidate and candidate.exists():
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            for key, value in user_cfg.items():
                if key == "compressMode" and isinstance(value, dict):
                    cfg["compressMode"].update(value)
                else:
                    cfg[key] = value
            print(f"[distill] Loaded config: {candidate}")
        except (json.JSONDecodeError, OSError) as e:
            print(f"[distill] WARNING: could not parse {candidate} ({e}); using defaults.")
    else:
        print("[distill] No .distill.json found — using built-in defaults.")

    # Always append safety excludes so the tool never re-ingests its own
    # output, regardless of what the user's config specified.
    existing = set(cfg.get("excludePatterns", []))
    for pat in ALWAYS_EXCLUDE:
        if pat not in existing:
            cfg.setdefault("excludePatterns", []).append(pat)

    return cfg


# --------------------------------------------------------------------------
# File discovery
# --------------------------------------------------------------------------

def is_excluded(rel_path: str, patterns: list[str]) -> bool:
    normalized = rel_path.replace(os.sep, "/")
    for pattern in patterns:
        if fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch("/" + normalized, pattern):
            return True
        # also match bare filename for simple patterns like "*.min.js"
        if fnmatch.fnmatch(os.path.basename(normalized), pattern):
            return True
    return False


def discover_files(root: Path, cfg: dict) -> list[Path]:
    exclude_patterns = cfg.get("excludePatterns", [])
    include_exts = set(cfg.get("includeExtensions", []))
    max_size = cfg.get("maxFileSizeBytes", DEFAULT_CONFIG["maxFileSizeBytes"])

    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        # Prune excluded directories in-place so os.walk doesn't descend into them.
        pruned = []
        for d in dirnames:
            rel = os.path.normpath(os.path.join(rel_dir, d)) if rel_dir != "." else d
            if is_excluded(rel + "/", exclude_patterns) or is_excluded(rel, exclude_patterns):
                continue
            pruned.append(d)
        dirnames[:] = pruned

        for fname in filenames:
            rel = os.path.normpath(os.path.join(rel_dir, fname)) if rel_dir != "." else fname
            if is_excluded(rel, exclude_patterns):
                continue

            ext = os.path.splitext(fname)[1].lower()
            if include_exts and ext not in include_exts:
                continue

            full_path = Path(dirpath) / fname
            try:
                size = full_path.stat().st_size
            except OSError:
                continue
            if size > max_size:
                continue
            if size == 0:
                continue

            found.append(full_path)

    return found


def order_with_priority(files: list[Path], root: Path, priority_files: list[str]) -> list[Path]:
    """Move priority files (matched by filename or relative-path suffix) to the front,
    preserving the given priority order, then the rest alphabetically."""
    priority_set = [p.replace("\\", "/") for p in priority_files]

    def priority_rank(f: Path) -> int:
        rel = str(f.relative_to(root)).replace("\\", "/")
        for i, pat in enumerate(priority_set):
            if rel == pat or rel.endswith("/" + pat) or f.name == pat:
                return i
        return len(priority_set) + 1

    return sorted(files, key=lambda f: (priority_rank(f), str(f.relative_to(root)).lower()))


# --------------------------------------------------------------------------
# Compression
# --------------------------------------------------------------------------

C_STYLE_EXTS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".kt",
                 ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".swift",
                 ".css", ".scss", ".php"}
HASH_COMMENT_EXTS = {".py", ".sh", ".bash", ".rb", ".yaml", ".yml", ".toml"}
SQL_EXTS = {".sql"}
HTML_EXTS = {".html", ".md", ".mdx"}


def strip_comments(content: str, ext: str) -> str:
    """Remove comments for common languages. Deliberately conservative:
    it does not attempt to parse strings perfectly for every edge case,
    but avoids the common failure mode of eating URLs like http://."""
    if ext in C_STYLE_EXTS:
        # Remove /* ... */ block comments (non-greedy, DOTALL)
        content = re.sub(r"/\*[\s\S]*?\*/", "", content)
        # Remove // line comments, but not inside strings is hard without a
        # full parser; this simple heuristic skips lines that look like URLs.
        def _strip_line_comment(line: str) -> str:
            # Skip if // appears to be part of a URL (http:// or https://)
            idx = line.find("//")
            while idx != -1:
                before = line[:idx]
                if before.rstrip().endswith(("http:", "https:")):
                    idx = line.find("//", idx + 2)
                    continue
                # crude string-awareness: count unescaped quotes before idx
                if before.count('"') % 2 == 0 and before.count("'") % 2 == 0 and before.count("`") % 2 == 0:
                    return line[:idx]
                idx = line.find("//", idx + 2)
            return line
        content = "\n".join(_strip_line_comment(line) for line in content.split("\n"))

    elif ext in HASH_COMMENT_EXTS:
        def _strip_hash_comment(line: str) -> str:
            idx = line.find("#")
            if idx == -1:
                return line
            before = line[:idx]
            if before.count('"') % 2 == 0 and before.count("'") % 2 == 0:
                return line[:idx]
            return line
        content = "\n".join(_strip_hash_comment(line) for line in content.split("\n"))
        # Python docstrings (triple-quoted) — only stripped if they appear to
        # be standalone documentation strings, not assigned to a variable.
        content = re.sub(r'^\s*"""[\s\S]*?"""', "", content, flags=re.MULTILINE)
        content = re.sub(r"^\s*'''[\s\S]*?'''", "", content, flags=re.MULTILINE)

    elif ext in SQL_EXTS:
        content = re.sub(r"/\*[\s\S]*?\*/", "", content)
        content = re.sub(r"--[^\n]*", "", content)

    return content


def strip_whitespace(content: str) -> str:
    """Collapse runs of blank lines and trim trailing whitespace per line."""
    lines = [line.rstrip() for line in content.split("\n")]
    out: list[str] = []
    blank_run = 0
    for line in lines:
        if line.strip() == "":
            blank_run += 1
            if blank_run > 1:
                continue
        else:
            blank_run = 0
        out.append(line)
    return "\n".join(out).strip("\n") + "\n"


def strip_types(content: str, ext: str) -> str:
    """Best-effort removal of TypeScript type annotations. This is a regex-
    based approximation (not a full TS compiler pass) intended to shrink
    token count for LLM context, not to produce valid re-compilable JS.
    Only applied to .ts/.tsx files."""
    if ext not in (".ts", ".tsx"):
        return content

    # Remove `: Type` annotations on params/vars (avoid ternaries by requiring
    # a preceding identifier/paren/bracket and not matching `::`).
    content = re.sub(r"(?<![:\w])\b(\w+)\s*:\s*[\w\[\]<>.,\s|&]+(?=[,)=;])", r"\1", content)
    # Remove interface / type alias declarations entirely.
    content = re.sub(r"^\s*export\s+interface\s+\w+[\s\S]*?\n\}\s*\n", "", content, flags=re.MULTILINE)
    content = re.sub(r"^\s*interface\s+\w+[\s\S]*?\n\}\s*\n", "", content, flags=re.MULTILINE)
    content = re.sub(r"^\s*export\s+type\s+\w+\s*=.*?;\s*\n", "", content, flags=re.MULTILINE)
    content = re.sub(r"^\s*type\s+\w+\s*=.*?;\s*\n", "", content, flags=re.MULTILINE)
    # Remove generic type params like <T, U> after function/class names.
    content = re.sub(r"(\w)<[\w\s,]+>(\s*\()", r"\1\2", content)
    # Remove function return-type annotations: `): Type {` -> `) {`
    content = re.sub(r"\)\s*:\s*[\w\[\]<>.,\s|&]+?\s*\{", ") {", content)
    content = re.sub(r"\)\s*:\s*[\w\[\]<>.,\s|&]+?\s*=>", ") =>", content)
    # Remove `as Type` casts.
    content = re.sub(r"\s+as\s+[\w\[\]<>.]+", "", content)
    return content


def compress_content(content: str, ext: str, mode: dict) -> str:
    if mode.get("stripComments"):
        content = strip_comments(content, ext)
    if mode.get("stripTypes"):
        content = strip_types(content, ext)
    if mode.get("stripWhitespace"):
        content = strip_whitespace(content)
    return content


# --------------------------------------------------------------------------
# Bundle building
# --------------------------------------------------------------------------

@dataclass
class FileResult:
    rel_path: str
    raw_content: str
    compressed_content: str
    raw_tokens: int = 0
    compressed_tokens: int = 0
    skipped_reason: Optional[str] = None


def xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def read_file_safely(path: Path) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        try:
            with open(path, "r", encoding="latin-1") as f:
                return f.read()
        except OSError:
            return None
    except OSError:
        return None


def build_bundle(root: Path, cfg: dict, dry_run: bool = False) -> list[FileResult]:
    files = discover_files(root, cfg)
    files = order_with_priority(files, root, cfg.get("priorityFiles", []))

    results: list[FileResult] = []
    for path in files:
        rel = str(path.relative_to(root)).replace(os.sep, "/")
        raw = read_file_safely(path)
        if raw is None:
            results.append(FileResult(rel, "", "", 0, 0, skipped_reason="unreadable/binary"))
            continue

        ext = path.suffix.lower()
        try:
            compressed = compress_content(raw, ext, cfg.get("compressMode", {}))
        except re.error as e:
            # Never let a regex edge-case crash the whole run.
            print(f"[distill] WARNING: compression failed for {rel} ({e}); using raw content.")
            compressed = raw

        results.append(
            FileResult(
                rel_path=rel,
                raw_content=raw,
                compressed_content=compressed,
                raw_tokens=count_tokens(raw),
                compressed_tokens=count_tokens(compressed),
            )
        )
    return results


def write_xml_bundle(results: list[FileResult], out_path: Path) -> None:
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', "<codebase>"]
    for r in results:
        if r.skipped_reason:
            continue
        parts.append(f'  <file path="{xml_escape(r.rel_path)}">')
        parts.append(xml_escape(r.compressed_content))
        parts.append("  </file>")
    parts.append("</codebase>\n")
    out_path.write_text("\n".join(parts), encoding="utf-8")


def write_json_bundle(results: list[FileResult], out_path: Path) -> None:
    payload = {
        "codebase": [
            {"path": r.rel_path, "content": r.compressed_content}
            for r in results
            if not r.skipped_reason
        ]
    }
    out_path.write_text(json.dumps(payload, indent=None, ensure_ascii=False), encoding="utf-8")


def write_report(results: list[FileResult], cfg: dict, out_path: Path, elapsed: float, fmt: str) -> None:
    included = [r for r in results if not r.skipped_reason]
    skipped = [r for r in results if r.skipped_reason]

    total_raw_chars = sum(len(r.raw_content) for r in included)
    total_compressed_chars = sum(len(r.compressed_content) for r in included)
    total_raw_tokens = sum(r.raw_tokens for r in included)
    total_compressed_tokens = sum(r.compressed_tokens for r in included)

    char_savings_pct = (
        100 * (1 - total_compressed_chars / total_raw_chars) if total_raw_chars else 0
    )
    token_savings_pct = (
        100 * (1 - total_compressed_tokens / total_raw_tokens) if total_raw_tokens else 0
    )

    lines = []
    lines.append("# Token Savings Report")
    lines.append("")
    lines.append(f"- **Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- **Token counting method:** {TOKEN_METHOD}")
    lines.append(f"- **Files included:** {len(included)}")
    lines.append(f"- **Files skipped (unreadable/binary):** {len(skipped)}")
    lines.append(f"- **Output format:** {fmt}")
    lines.append(f"- **Run time:** {elapsed:.2f}s")
    lines.append("")
    lines.append("## Totals")
    lines.append("")
    lines.append("| Metric | Raw | Compressed | Saved |")
    lines.append("|---|---:|---:|---:|")
    lines.append(
        f"| Characters | {total_raw_chars:,} | {total_compressed_chars:,} | {char_savings_pct:.1f}% |"
    )
    lines.append(
        f"| Tokens (est.) | {total_raw_tokens:,} | {total_compressed_tokens:,} | {token_savings_pct:.1f}% |"
    )
    lines.append("")

    lines.append("## Per-file breakdown")
    lines.append("")
    lines.append("| File | Raw tokens | Compressed tokens | Saved |")
    lines.append("|---|---:|---:|---:|")
    for r in sorted(included, key=lambda x: x.raw_tokens, reverse=True)[:50]:
        saved = 100 * (1 - r.compressed_tokens / r.raw_tokens) if r.raw_tokens else 0
        lines.append(f"| `{r.rel_path}` | {r.raw_tokens:,} | {r.compressed_tokens:,} | {saved:.0f}% |")
    if len(included) > 50:
        lines.append(f"| ...and {len(included) - 50} more files | | | |")
    lines.append("")

    if skipped:
        lines.append("## Skipped files")
        lines.append("")
        for r in skipped[:30]:
            lines.append(f"- `{r.rel_path}` — {r.skipped_reason}")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="distill",
        description="Bundle a codebase into a compressed, LLM-ready context file.",
    )
    parser.add_argument("--root", default=".", help="Project root to scan (default: current directory)")
    parser.add_argument("--config", default=None, help="Path to a specific config file")
    parser.add_argument("--format", choices=["xml", "json"], default=None, help="Output bundle format")
    parser.add_argument("--output", default=None, help="Output bundle file path")
    parser.add_argument("--report", default=None, help="Output report file path")
    parser.add_argument("--dry-run", action="store_true", help="Scan and report without writing the bundle file")
    args = parser.parse_args(argv)

    start = time.time()
    root = Path(args.root).resolve()
    if not root.exists() or not root.is_dir():
        print(f"[distill] ERROR: root path does not exist or is not a directory: {root}", file=sys.stderr)
        return 1

    cfg = load_config(root, args.config)

    fmt = args.format or ("json" if cfg.get("outputFile", "").endswith(".json") else "xml")
    out_name = args.output or cfg.get("outputFile", "context-bundle.xml")
    if args.format and not args.output:
        # keep extension consistent with chosen format if user overrode format only
        out_name = f"context-bundle.{fmt}"
    report_name = args.report or cfg.get("reportFile", "token-savings-report.md")

    print(f"[distill] Scanning {root} ...")
    try:
        results = build_bundle(root, cfg, dry_run=args.dry_run)
    except KeyboardInterrupt:
        print("\n[distill] Aborted by user.")
        return 130

    included = [r for r in results if not r.skipped_reason]
    print(f"[distill] Found {len(included)} files to include "
          f"({len(results) - len(included)} skipped).")

    if not included:
        print("[distill] No files matched — check your excludePatterns/includeExtensions in config.")

    out_path = root / out_name
    report_path = root / report_name

    if not args.dry_run:
        if fmt == "json":
            write_json_bundle(results, out_path)
        else:
            write_xml_bundle(results, out_path)
        print(f"[distill] Bundle written to {out_path}")
    else:
        print("[distill] Dry run — bundle file not written.")

    elapsed = time.time() - start
    write_report(results, cfg, report_path, elapsed, fmt if not args.dry_run else f"{fmt} (dry-run)")
    print(f"[distill] Report written to {report_path}")

    total_raw_tokens = sum(r.raw_tokens for r in included)
    total_compressed_tokens = sum(r.compressed_tokens for r in included)
    if total_raw_tokens:
        pct = 100 * (1 - total_compressed_tokens / total_raw_tokens)
        print(f"[distill] Estimated token savings: {pct:.1f}% "
              f"({total_raw_tokens:,} -> {total_compressed_tokens:,})")

    return 0


if __name__ == "__main__":
    sys.exit(main())