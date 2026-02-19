import re
import logging
from typing import List

logger = logging.getLogger(__name__)

IGNORE_PATTERNS = [
    r"\.lock$", r"\.min\.js$", r"\.min\.css$",
    r"package-lock\.json$", r"yarn\.lock$", r"poetry\.lock$",
    r"\.pyc$", r"\.pem$",
    r"\.png$", r"\.jpg$", r"\.jpeg$", r"\.gif$", r"\.svg$", r"\.pdf$",
    r"migrations/.*\.py$", r"__pycache__/",
    r"requirements\.txt$", r"\.txt$",
]

MAX_CHUNK_CHARS = 4000


def _should_skip_file(filepath: str) -> bool:
    for pattern in IGNORE_PATTERNS:
        if re.search(pattern, filepath):
            return True
    return False


def _split_diff_by_file(diff: str) -> List[tuple]:
    file_diffs = re.split(r"(?=diff --git )", diff.strip())
    result = []
    for file_diff in file_diffs:
        if not file_diff.strip():
            continue
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
    hunks = re.split(r"(?=@@)", file_diff)
    header = hunks[0]
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
            sub_chunks = _split_large_file_diff(filepath, file_diff)
            for i, sub_chunk in enumerate(sub_chunks):
                chunks.append({
                    "chunk_id": f"{filepath}:{i}",
                    "file": filepath,
                    "content": sub_chunk,
                    "size": len(sub_chunk),
                })
    total_size = sum(c["size"] for c in chunks)
    logger.info(f"[chunker] Split diff into {len(chunks)} chunks across {len(file_diffs)} files ({total_size} total chars)")
    return chunks