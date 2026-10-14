import json
import re
from pathlib import Path

from knowledge_rag.processing import normalize


def seconds(value):
    components = value.replace(",", ".").split(":")
    return sum(float(part) * 60**i for i, part in enumerate(reversed(components)))


def parse_subtitles(text):
    segments = []
    lines = text.replace("\r\n", "\n").splitlines()
    for i, line in enumerate(lines):
        match = re.match(r"([\d:,.]+)\s*-->\s*([\d:,.]+)", line)
        if not match:
            continue
        start, end = map(seconds, match.groups())
        if end < start or start < 0:
            raise ValueError("Invalid subtitle interval")
        body = []
        for subsequent in lines[i + 1 :]:
            if not subsequent.strip():
                break
            body.append(subsequent)
        value = normalize(re.sub(r"<[^>]+>", "", " ".join(body)))
        if value:
            segments.append({"text": value, "start": start, "duration": end - start})
    if not segments:
        raise ValueError("No subtitle segments found")
    return segments


def read_manual(path, source_key=None, title=None, url="", video_id=None):
    path = Path(path)
    if path.stat().st_size > 5_000_000:
        raise ValueError("Manual imports limited to 5 MB")
    content = path.read_text(encoding="utf-8-sig")
    suffix = path.suffix.lower()
    if suffix == ".json":
        value = json.loads(content)
        if not isinstance(value, dict):
            raise ValueError("JSON import must be a document object")
        return value
    data = {
        "source_key": source_key or f"manual:{path.name}",
        "source_type": "manual",
        "title": title or path.stem,
        "url": url,
        "text": content,
        "metadata": {"provider": "manual", "language": "unknown"},
    }
    if suffix in (".srt", ".vtt"):
        data["segments"] = parse_subtitles(content)
    elif suffix not in (".txt", ".md"):
        raise ValueError("Supported imports: TXT, MD, SRT, VTT, JSON")
    if video_id:
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError("Invalid YouTube video ID")
        data["source_key"] = f"youtube:{video_id}"
        data["source_type"] = "youtube_transcript"
        data["url"] = f"https://www.youtube.com/watch?v={video_id}"
    return data
