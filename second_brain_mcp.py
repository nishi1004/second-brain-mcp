"""
Second Brain MCP Server

知識層（Second Brain）とプロジェクト層を接続する第3層のインターフェース。
Senseモデルに基づき、知識の検索・最近の知見取得・気づきの還元を提供する。
"""

import argparse
import logging
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# --- Configuration ---

VAULT_PATH: str = ""
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


# --- Embedding / Semantic Search ---

_embedding_model = None
_embeddings_cache: dict[str, tuple[float, np.ndarray]] = {}  # path -> (mtime, vector)

EMBEDDING_MODEL_NAME = "intfloat/multilingual-e5-small"


def _get_model():
    """Lazy-load the embedding model on first use."""
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer

        logger.info("Loading embedding model: %s", EMBEDDING_MODEL_NAME)
        _embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        logger.info("Embedding model loaded.")
    return _embedding_model


def _embedding_text(note: dict) -> str:
    """Build text to embed from note metadata."""
    parts = []
    if note.get("title"):
        parts.append(note["title"])
    for p in note.get("abstract_principles", []):
        parts.append(p)
    for d in note.get("applicable_domains", []):
        parts.append(d.replace("-", " "))
    return " ".join(parts)


def _build_embeddings(notes: list[dict]) -> None:
    """Build/update embedding cache (only recompute changed files)."""
    model = _get_model()
    to_encode: list[tuple[str, str]] = []  # (path, text)

    for note in notes:
        path = note["_path"]
        filepath = Path(VAULT_PATH) / path
        try:
            mtime = filepath.stat().st_mtime
        except OSError:
            continue

        if path in _embeddings_cache and _embeddings_cache[path][0] == mtime:
            continue  # cache hit

        to_encode.append((path, "passage: " + _embedding_text(note)))
        # store mtime now, vector will be filled after batch encode
        _embeddings_cache[path] = (mtime, np.array([]))

    if to_encode:
        paths, texts = zip(*to_encode)
        vectors = model.encode(list(texts), normalize_embeddings=True)
        for path, vec in zip(paths, vectors):
            mtime = _embeddings_cache[path][0]
            _embeddings_cache[path] = (mtime, vec)


def _semantic_rank(
    context: str, notes: list[dict], top_k: int = 10
) -> list[dict]:
    """Rank notes by cosine similarity to context, return top_k."""
    _build_embeddings(notes)
    model = _get_model()

    query_vec = model.encode("query: " + context, normalize_embeddings=True)

    scored = []
    for note in notes:
        path = note["_path"]
        if path not in _embeddings_cache:
            continue
        _, note_vec = _embeddings_cache[path]
        if note_vec.size == 0:
            continue
        score = float(query_vec @ note_vec)
        scored.append((score, note))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [note for _, note in scored[:top_k]]


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
    top_k: int = 10,
) -> str:
    """Search Second Brain for knowledge related to the given context.

    Uses semantic similarity to rank notes by relevance to context.

    Args:
        context: Current context or challenge in natural language.
        domains: Optional domain tags to filter by
                 (e.g. game-design, software-architecture, skill-acquisition).
        depth: Detail level - "index" (frontmatter only),
               "summary" (+ core thesis & principles), or "full" (entire note).
        top_k: Maximum number of results to return (default 10, 0 for all).
    """
    notes = _scan_notes()
    filtered = _filter_by_domains(notes, domains or [])

    # Semantic ranking by context
    if context.strip() and top_k > 0:
        ranked = _semantic_rank(context, filtered, top_k=top_k)
    elif context.strip():
        ranked = _semantic_rank(context, filtered, top_k=len(filtered))
    else:
        ranked = filtered

    if depth == "full":
        results = [_format_full(n) for n in ranked]
    elif depth == "summary":
        results = [_format_summary(n) for n in ranked]
    else:
        results = [_format_index(n) for n in ranked]

    if not results:
        return "No matching notes found in Second Brain."

    output_parts = [f"Found {len(results)} notes (top {len(results)} by relevance):\n"]
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


def _resolve_vault_path(cli_vault: str | None = None) -> str:
    """Resolve vault path from CLI arg, env var, or auto-detection."""
    # 1. CLI argument (highest priority)
    if cli_vault:
        path = Path(cli_vault).expanduser().resolve()
        if _is_vault(path):
            return str(path)
        raise SystemExit(f"Not a valid Second Brain vault: {path}")

    # 2. Environment variable
    env_path = os.environ.get("SECOND_BRAIN_PATH")
    if env_path:
        path = Path(env_path).expanduser().resolve()
        if _is_vault(path):
            return str(path)
        raise SystemExit(f"SECOND_BRAIN_PATH is not a valid vault: {path}")

    # 3. Auto-detect: search common locations
    candidates = [
        Path.home() / "Documents" / "second-brain" / "second-brain",
        Path.home() / "second-brain",
        Path.home() / "Documents" / "second-brain",
    ]
    for candidate in candidates:
        if _is_vault(candidate):
            return str(candidate)

    raise SystemExit(
        "Could not find Second Brain vault. "
        "Pass --vault <path> or set SECOND_BRAIN_PATH."
    )


def _is_vault(path: Path) -> bool:
    """Check if a directory looks like a Second Brain vault."""
    if not path.is_dir():
        return False
    markers = ["00_Inbox", "30_Areas", "50_Tech_Notes"]
    return any((path / m).is_dir() for m in markers)


def main() -> None:
    parser = argparse.ArgumentParser(description="Second Brain MCP Server")
    parser.add_argument("--vault", help="Path to Second Brain vault")
    parser.add_argument(
        "--git-sync",
        action="store_true",
        default=None,
        help="Enable git sync with remote",
    )
    args = parser.parse_args()

    global VAULT_PATH, GIT_SYNC
    VAULT_PATH = _resolve_vault_path(args.vault)
    if args.git_sync is not None:
        GIT_SYNC = args.git_sync

    mcp.run()


if __name__ == "__main__":
    main()
