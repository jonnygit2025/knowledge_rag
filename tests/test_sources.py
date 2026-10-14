import socket
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import func, select

from knowledge_rag.ingestion.manual import parse_subtitles
from knowledge_rag.ingestion.web import UnsafeURL, canonical_url, public_addresses
from knowledge_rag.ingestion.youtube import TranscriptUnavailable, YouTubeMetadata
from knowledge_rag.jobs import acquire_transcript, sync_channels
from knowledge_rag.models import Document, Job, Video
from knowledge_rag.providers import BudgetExceeded, Providers


def test_subtitles_preserve_timing_and_remove_visible_timestamps():
    text = "WEBVTT\n\n00:00:12.000 --> 00:00:15.500\n<b>Qdrant</b> indicizza.\n\n"
    parsed = parse_subtitles(text)
    assert parsed == [{"text": "Qdrant indicizza.", "start": 12.0, "duration": 3.5}]
    with pytest.raises(ValueError):
        parse_subtitles("no timestamp")


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/", "http://10.0.0.1/"]
)
def test_private_sources_rejected(url):
    with pytest.raises(UnsafeURL):
        public_addresses(url)


def test_urls_preserve_query_and_reject_mixed_dns(monkeypatch):
    assert canonical_url("https://example.com/a?version=2#top") == "https://example.com/a?version=2"
    with pytest.raises(UnsafeURL):
        canonical_url("file:///etc/passwd")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [(0, 0, 0, "", ("8.8.8.8", 443)), (0, 0, 0, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(UnsafeURL):
        public_addresses("https://example.com")


class MetadataFixture:
    def resolve_channel(self, ref):
        return {"id": "UCfixture", "name": "Fixture", "playlist": "UUfixture"}

    def videos(self, *args, **kwargs):
        for vid in ("abcdefghijk", "lmnopqrstuv"):
            yield {
                "youtube_video_id": vid,
                "title": vid,
                "description": "",
                "published_at": datetime.now(UTC),
                "url": f"https://www.youtube.com/watch?v={vid}",
                "is_live": False,
                "duration": "PT1M",
            }


def test_sync_and_missing_transcript_do_not_block_other_video(factory, cfg):
    cfg.config_file.write_text('channels:\n  - reference: "@fixture"\n', encoding="utf-8")
    sync_channels(factory, cfg, MetadataFixture())
    sync_channels(factory, cfg, MetadataFixture())
    with factory() as session:
        videos = session.scalars(select(Video).order_by(Video.youtube_video_id)).all()
        assert len(videos) == session.scalar(select(func.count()).select_from(Job)) == 2

    class Missing:
        def fetch(self, *args):
            raise TranscriptUnavailable("no_transcript", "No transcript")

    class Available:
        def fetch(self, *args):
            return {
                "segments": [{"text": "Qdrant indicizza dati.", "start": 4, "duration": 2}],
                "metadata": {"language": "it", "provider": "fixture"},
            }

    acquire_transcript(factory, cfg, str(videos[0].id), Missing())
    acquire_transcript(factory, cfg, str(videos[1].id), Available())
    with factory() as session:
        assert session.get(Video, videos[0].id).acquisition_status == "no_transcript"
        assert session.get(Video, videos[1].id).acquisition_status == "ready"
        assert session.scalar(
            select(Document).where(Document.video_id == videos[1].id)
        ).active_version_id


def test_pagination_finds_all_videos():
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path.endswith("playlistItems"):
            page = request.url.params.get("pageToken")
            vid = "second" if page else "first"
            result = {"items": [{"contentDetails": {"videoId": vid}}]}
            if not page:
                result["nextPageToken"] = "page2"
        else:
            vid = request.url.params["id"]
            result = {
                "items": [
                    {
                        "id": vid,
                        "snippet": {"title": vid, "publishedAt": datetime.now(UTC).isoformat()},
                    }
                ]
            }
        return httpx.Response(200, json=result)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        adapter = YouTubeMetadata("test-key", client)
        result = list(adapter.videos("playlist", initial=False, max_initial=1))
    assert [item["youtube_video_id"] for item in result] == ["first", "second"]
    assert len(calls) == 4


def test_provider_budget_and_cache(factory, cfg):
    cfg = cfg.model_copy(
        update={
            "provider_api_key": __import__("pydantic").SecretStr("dummy"),
            "max_paid_calls_per_day": 1,
        }
    )
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Test [1]"}}], "usage": {"total_tokens": 12}},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = Providers(cfg, factory, client)
        assert provider.paid("chat", "fixture-model", {"messages": []})
        assert provider.paid("chat", "fixture-model", {"messages": []})
        with pytest.raises(BudgetExceeded):
            provider.paid("chat", "fixture-model", {"messages": ["different"]})
    assert len(requests) == 1
