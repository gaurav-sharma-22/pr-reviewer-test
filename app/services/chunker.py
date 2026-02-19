import re
import logging
from typing import List

logger = logging.getLogger(__name__)

# Files to skip — generated, binary, or irrelevant
IGNORE_PATTERNS = [
    r"\.lock$",
    r"\.min\.js$",
    r"\.min\.css$",
    r"package-lock\.json$",
    r"yarn\.lock$",
    r"poetry\.lock$",
    r"\.pyc$",
    r"\.pem$",
    r"\.png$", r"\.jpg$", r"\.jpeg$", r"\.gif$", r"\.svg$",
    r"\.pdf$",
    r"migrations/.*\.py$",
    r"__pycache__/",
    r"trigger\.txt$",
    r"\.txt$",  # or just ignore all txt files
]

MAX_CHUNK_CHARS = 4000


def _should_skip_file(filepath: str) -> bool:
    for pattern in IGNORE_PATTERNS:
        if re.search(pattern, filepath):
            return True
    return False


def _split_diff_by_file(diff: str) -> List[tuple]:
    """Split a unified diff into (filepath, file_diff) tuples."""
    # Each file diff starts with 'diff --git a/...'
    file_diffs = re.split(r"(?=diff --git )", diff.strip())
    result = []

    for file_diff in file_diffs:
        if not file_diff.strip():
            continue

        # Extract filepath from the diff header
        match = re.search(r"diff --git a/(.+?) b/", file_diff)
        if not match:
            continue

        filepath = match.group(1)

        if _should_skip_file(filepath):
            logger.info(f"[chunker] Skipping file: {filepath}")
            continue

        result.append((filepath, file_diff))

    return result


def _split_large_file_diff(filepath: str, file_diff: str) -> List[str]:
    """Split a large file diff into smaller chunks by hunk boundaries."""
    # Hunks start with '@@'
    hunks = re.split(r"(?=@@)", file_diff)
    header = hunks[0]  # diff --git header lines

    chunks = []
    current_chunk = header

    for hunk in hunks[1:]:
        if len(current_chunk) + len(hunk) > MAX_CHUNK_CHARS:
            if current_chunk.strip():
                chunks.append(current_chunk)
            current_chunk = header + hunk
        else:
            current_chunk += hunk

    if current_chunk.strip():
        chunks.append(current_chunk)

    return chunks


def chunk_diff(diff: str) -> List[dict]:
    """
    Split a PR diff into reviewable chunks.

    Returns list of:
    {
        "chunk_id": "file_path:chunk_index",
        "file": "path/to/file.py",
        "content": "diff content",
        "size": 1234
    }
    """
    file_diffs = _split_diff_by_file(diff)
    chunks = []

    for filepath, file_diff in file_diffs:
        if len(file_diff) <= MAX_CHUNK_CHARS:
            chunks.append({
                "chunk_id": f"{filepath}:0",
                "file": filepath,
                "content": file_diff,
                "size": len(file_diff),
            })
        else:
            # Split large file diffs by hunk
            sub_chunks = _split_large_file_diff(filepath, file_diff)
            for i, sub_chunk in enumerate(sub_chunks):
                chunks.append({
                    "chunk_id": f"{filepath}:{i}",
                    "file": filepath,
                    "content": sub_chunk,
                    "size": len(sub_chunk),
                })

    total_size = sum(c["size"] for c in chunks)
    logger.info(
        f"[chunker] Split diff into {len(chunks)} chunks across "
        f"{len(file_diffs)} files ({total_size} total chars)"
    )

    return chunks