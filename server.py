"""
Second Brain MCP Server

知識層（Second Brain）とプロジェクト層を接続する第3層のインターフェース。
Senseモデルに基づき、知識の検索・最近の知見取得・気づきの還元を提供する。
"""

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

import yaml
from mcp.server.fastmcp import FastMCP

# --- Configuration ---

VAULT_PATH = os.environ.get(
    "SECOND_BRAIN_PATH",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)
KNOWLEDGE_DIRS = ["30_Areas", "40_Resources", "50_Tech_Notes"]
GIT_SYNC = os.environ.get("SECOND_BRAIN_GIT_SYNC", "false").lower() == "true"

mcp = FastMCP("second-brain")

# --- Helpers ---


def _git_pull() -> None:
    """Pull latest from remote if git sync is enabled."""
    if not GIT_SYNC:
        return
    try:
        subprocess.run(
            ["git", "pull", "--rebase", "--quiet"],
            cwd=VAULT_PATH,
            capture_output=True,
            timeout=30,
        )
    except Exception:
        pass  # Non-fatal: work with local state


def _git_commit_and_push(filepath: str, message: str) -> None:
    """Commit a file and push if git sync is enabled."""
    if not GIT_SYNC:
        return
    try:
        subprocess.run(["git", "add", filepath], cwd=VAULT_PATH, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", message], cwd=VAULT_PATH, capture_output=True
        )
        subprocess.run(
            ["git", "push"], cwd=VAULT_PATH, capture_output=True, timeout=30
        )
    except Exception:
        pass


def _parse_frontmatter(filepath: Path) -> dict | None:
    """Parse YAML frontmatter from a markdown file."""
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception:
        return None

    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not match:
        return None

    try:
        meta = yaml.safe_load(match.group(1))
        if not isinstance(meta, dict):
            return None
        meta["_body"] = text[match.end() :]
        meta["_path"] = str(filepath.relative_to(VAULT_PATH))
        return meta
    except yaml.YAMLError:
        return None


def _extract_summary(body: str) -> str:
    """Extract 核心の命題 and 抽出された原則 sections from note body."""
    lines = body.split("\n")
    sections: list[str] = []
    capturing = False

    for line in lines:
        if re.match(r"^##\s*(核心の命題|核心|抽出された原則|構造)", line):
            capturing = True
            sections.append(line)
        elif re.match(r"^##\s", line):
            capturing = False
        elif capturing:
            sections.append(line)

    return "\n".join(sections).strip() if sections else body[:500]


def _scan_notes() -> list[dict]:
    """Scan vault for notes with frontmatter."""
    _git_pull()
    vault = Path(VAULT_PATH)
    notes = []

    for dir_name in KNOWLEDGE_DIRS:
        dir_path = vault / dir_name
        if not dir_path.exists():
            continue
        for md_file in dir_path.rglob("*.md"):
            meta = _parse_frontmatter(md_file)
            if meta and meta.get("abstract_principles"):
                notes.append(meta)

    return notes


def _filter_by_domains(notes: list[dict], domains: list[str]) -> list[dict]:
    """Filter notes by applicable_domains overlap."""
    if not domains:
        return notes
    domain_set = set(d.lower() for d in domains)
    return [
        n
        for n in notes
        if domain_set & set(d.lower() for d in n.get("applicable_domains", []))
    ]


def _format_index(note: dict) -> dict:
    """Format note as index-level response."""
    return {
        "title": note.get("title", "Untitled"),
        "path": note.get("_path", ""),
        "abstract_principles": note.get("abstract_principles", []),
        "applicable_domains": note.get("applicable_domains", []),
    }


def _format_summary(note: dict) -> dict:
    """Format note as summary-level response."""
    result = _format_index(note)
    result["summary"] = _extract_summary(note.get("_body", ""))
    return result


def _format_full(note: dict) -> dict:
    """Format note as full response."""
    result = _format_index(note)
    result["content"] = note.get("_body", "")
    return result


# --- Tools ---


@mcp.tool()
def query_knowledge(
    context: str,
    domains: list[str] | None = None,
    depth: str = "index",
) -> str:
    """Search Second Brain for knowledge related to the given context.

    Args:
        context: Current context or challenge in natural language.
        domains: Optional domain tags to filter by
                 (e.g. game-design, software-architecture, skill-acquisition).
        depth: Detail level - "index" (frontmatter only),
               "summary" (+ core thesis & principles), or "full" (entire note).
    """
    notes = _scan_notes()
    filtered = _filter_by_domains(notes, domains or [])

    if depth == "full":
        results = [_format_full(n) for n in filtered]
    elif depth == "summary":
        results = [_format_summary(n) for n in filtered]
    else:
        results = [_format_index(n) for n in filtered]

    if not results:
        return "No matching notes found in Second Brain."

    output_parts = [f"Found {len(results)} notes matching query:\n"]
    for r in results:
        output_parts.append(f"## {r['title']}")
        output_parts.append(f"Path: {r['path']}")
        output_parts.append(
            f"Principles: {', '.join(r.get('abstract_principles', []))}"
        )
        output_parts.append(f"Domains: {', '.join(r.get('applicable_domains', []))}")
        if "summary" in r:
            output_parts.append(f"\n{r['summary']}")
        if "content" in r:
            output_parts.append(f"\n{r['content']}")
        output_parts.append("---")

    return "\n".join(output_parts)


@mcp.tool()
def get_recent(
    since: str,
    domains: list[str] | None = None,
) -> str:
    """Get notes created or updated since a given date.

    Args:
        since: ISO date string (e.g. "2026-03-10").
        domains: Optional domain tags to filter by.
    """
    try:
        since_date = datetime.fromisoformat(since).date()
    except ValueError:
        return f"Invalid date format: {since}. Use ISO format (YYYY-MM-DD)."

    notes = _scan_notes()
    filtered = _filter_by_domains(notes, domains or [])

    recent = []
    for n in filtered:
        created = n.get("created")
        if isinstance(created, str):
            try:
                note_date = datetime.fromisoformat(created).date()
            except ValueError:
                continue
        elif hasattr(created, "isoformat"):
            note_date = created
        else:
            continue

        if note_date >= since_date:
            recent.append(n)

    if not recent:
        return f"No notes found since {since}."

    output_parts = [f"Found {len(recent)} notes since {since}:\n"]
    for n in recent:
        info = _format_index(n)
        output_parts.append(f"- **{info['title']}** ({info['path']})")
        output_parts.append(
            f"  Principles: {', '.join(info.get('abstract_principles', []))}"
        )
        output_parts.append(
            f"  Domains: {', '.join(info.get('applicable_domains', []))}"
        )

    return "\n".join(output_parts)


@mcp.tool()
def capture_insight(
    title: str,
    content: str,
    abstract_principles: list[str],
    applicable_domains: list[str],
    source_project: str | None = None,
) -> str:
    """Capture an insight from a project back to Second Brain.

    Creates a new note in 00_Inbox/ with proper frontmatter.

    Args:
        title: Title of the insight note.
        content: Body content of the note (markdown).
        abstract_principles: Domain-independent abstract principles extracted.
        applicable_domains: Domain tags for this insight.
        source_project: Optional name of the source project.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[\s]+", "-", slug).strip("-")[:50]
    filename = f"{today}-{slug}.md"

    inbox_path = Path(VAULT_PATH) / "00_Inbox"
    inbox_path.mkdir(exist_ok=True)
    filepath = inbox_path / filename

    frontmatter = {
        "title": title,
        "created": today,
        "source": "external-capture",
        "abstract_principles": abstract_principles,
        "applicable_domains": applicable_domains,
    }
    if source_project:
        frontmatter["source_project"] = source_project

    fm_str = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False)
    note_content = f"---\n{fm_str}---\n\n{content}\n"

    filepath.write_text(note_content, encoding="utf-8")

    _git_commit_and_push(
        str(filepath.relative_to(VAULT_PATH)),
        f"Capture insight: {title}",
    )

    return f"Note created: {filepath.relative_to(VAULT_PATH)}"


if __name__ == "__main__":
    mcp.run()
