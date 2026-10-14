from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx
from youtube_transcript_api import (
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)


class TranscriptUnavailable(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class TranscriptProvider(Protocol):
    def fetch(self, video_id: str, languages: list[str]) -> dict: ...


class YouTubeTranscripts:
    def fetch(self, video_id, languages):
        try:
            tracks = list(YouTubeTranscriptApi().list(video_id))
            # Prefer configured language order; manual track wins within a language.
            preferred = sorted(
                tracks,
                key=lambda t: (
                    languages.index(t.language_code)
                    if t.language_code in languages
                    else len(languages),
                    t.is_generated,
                ),
            )
            if not preferred:
                raise TranscriptUnavailable("no_transcript", "No available transcript track")
            track = preferred[0]
            fetched = track.fetch()
            return {
                "segments": fetched.to_raw_data(),
                "metadata": {
                    "language": track.language_code,
                    "track_type": "auto" if track.is_generated else "manual",
                    "provider": "youtube-transcript-api",
                },
            }
        except TranscriptsDisabled as exc:
            raise TranscriptUnavailable("disabled", "Subtitles disabled") from exc
        except NoTranscriptFound as exc:
            raise TranscriptUnavailable("no_transcript", "No available transcript") from exc
        except RequestBlocked as exc:
            raise TranscriptUnavailable("blocked", "YouTube blocked this IP/request") from exc
        except VideoUnavailable as exc:
            raise TranscriptUnavailable("unavailable", "Video unavailable") from exc


class YouTubeMetadata:
    def __init__(self, api_key, client=None):
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=30)

    def close(self):
        self.client.close()

    def get(self, resource, **params):
        if not self.api_key:
            raise ValueError("YOUTUBE_API_KEY required for live channel synchronization")
        response = self.client.get(
            f"https://www.googleapis.com/youtube/v3/{resource}",
            params={"key": self.api_key, **params},
        )
        if response.status_code >= 400:
            # Never include the request URL because it contains the API key.
            raise RuntimeError(f"YouTube Data API HTTP {response.status_code}")
        return response.json()

    def resolve_channel(self, reference):
        if reference.startswith("UC"):
            selector = {"id": reference}
        elif reference.startswith("@"):
            selector = {"forHandle": reference}
        else:
            raise ValueError("Configure a UC... channel ID or @handle")
        items = self.get("channels", part="snippet,contentDetails", **selector).get("items", [])
        if not items:
            raise ValueError("Channel not found")
        item = items[0]
        return {
            "id": item["id"],
            "name": item["snippet"]["title"],
            "playlist": item["contentDetails"]["relatedPlaylists"]["uploads"],
        }

    def videos(self, playlist_id, days=30, max_initial=10, initial=False):
        cutoff = datetime.now(UTC) - timedelta(days=days)
        token, count = None, 0
        while True:
            params = {"part": "contentDetails", "playlistId": playlist_id, "maxResults": 50}
            if token:
                params["pageToken"] = token
            page = self.get("playlistItems", **params)
            ids = [item["contentDetails"]["videoId"] for item in page.get("items", [])]
            if not ids:
                return
            items = self.get(
                "videos", part="snippet,contentDetails,liveStreamingDetails", id=",".join(ids)
            ).get("items", [])
            order = {vid: i for i, vid in enumerate(ids)}
            items.sort(key=lambda item: order[item["id"]])
            older = False
            for item in items:
                snippet = item["snippet"]
                date = datetime.fromisoformat(snippet["publishedAt"].replace("Z", "+00:00"))
                if date < cutoff:
                    older = True
                    continue
                yield {
                    "youtube_video_id": item["id"],
                    "title": snippet["title"],
                    "description": snippet.get("description", ""),
                    "published_at": date,
                    "url": f"https://www.youtube.com/watch?v={item['id']}",
                    "is_live": snippet.get("liveBroadcastContent") in ("live", "upcoming"),
                    "duration": item.get("contentDetails", {}).get("duration", ""),
                }
                count += 1
                if initial and max_initial and count >= max_initial:
                    return
            token = page.get("nextPageToken")
            if not token or older:
                return
