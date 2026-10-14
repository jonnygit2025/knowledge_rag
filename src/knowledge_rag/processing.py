import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def fingerprint(value) -> str:
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str))


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


@dataclass
class Processed:
    text: str
    corrections: list[dict]


def process_segment(text: str, glossary: dict[str, str]) -> Processed:
    current = normalize(text)
    corrections = []
    if current != text:
        corrections.append({"before": text, "after": current, "reason": "normalization-v1"})
    for before, after in glossary.items():
        # Only explicit, user-approved glossary entries, exact case, complete terms.
        replacement = re.sub(r"(?<!\w)" + re.escape(before) + r"(?!\w)", after, current)
        if replacement != current:
            corrections.append(
                {"before": current, "after": replacement, "reason": f"glossary:{before}"}
            )
            current = replacement
    return Processed(current, corrections)


def chunk_segments(processed: list[str], limit: int) -> list[tuple[str, list[int]]]:
    chunks = []
    buffer, indices = [], []
    for index, text in enumerate(processed):
        if not text:
            continue
        # Long segments are split but every resulting chunk retains the same original segment ID.
        pieces = [text[i : i + limit] for i in range(0, len(text), limit)]
        for piece in pieces:
            if buffer and len(" ".join(buffer + [piece])) > limit:
                chunks.append((" ".join(buffer), list(dict.fromkeys(indices))))
                buffer, indices = [], []
            buffer.append(piece)
            indices.append(index)
    if buffer:
        chunks.append((" ".join(buffer), list(dict.fromkeys(indices))))
    return chunks
