import base64
import ipaddress
import json
import re
import socket
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import httpx
import trafilatura


class UnsafeURL(ValueError):
    pass


def canonical_url(url):
    parsed = urlsplit(url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise UnsafeURL("Only public HTTP(S) URLs without credentials are allowed")
    if parsed.port not in (None, 80, 443):
        raise UnsafeURL("Only standard HTTP(S) ports are allowed")
    # Preserve parameters which may identify source content; drop only fragment.
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, "")
    )


def public_addresses(url):
    host = urlsplit(canonical_url(url)).hostname
    addresses = {item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise UnsafeURL("Source resolves to a private, local, or reserved address")
    return sorted(addresses)


def fetch_public(url, max_bytes=1_000_000):
    # Pin the validated IP for the actual request, preserving HTTP Host and TLS SNI.
    # Disable environment proxies to avoid bypassing public-address validation.
    with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
        for _ in range(6):
            url = canonical_url(url)
            host = urlsplit(url).hostname
            address = public_addresses(url)[0]
            target = httpx.URL(url).copy_with(host=address)
            with client.stream(
                "GET",
                target,
                headers={"Host": host, "User-Agent": "KnowledgeRAG/0.1"},
                extensions={"sni_hostname": host},
            ) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers["location"])
                    continue
                response.raise_for_status()
                data = bytearray()
                for part in response.iter_bytes():
                    data.extend(part)
                    if len(data) > max_bytes:
                        raise ValueError("Source exceeds byte limit")
                return bytes(data), url, response.headers.get("content-type", "")
    raise UnsafeURL("Too many redirects")


def extract_urls(text):
    result = []
    for url in re.findall(r"https?://[^\s<>\"']+", text):
        url = url.rstrip(".,;!)]}")
        try:
            result.append((url, canonical_url(url)))
        except ValueError:
            continue
    return list(dict.fromkeys(result))


def read_external(url):
    canonical = canonical_url(url)
    parts = urlsplit(canonical)
    segments = parts.path.strip("/").split("/")
    if parts.hostname == "github.com" and len(segments) == 2:
        owner, repo = segments
        repo = repo.removesuffix(".git")
        if not all(re.fullmatch(r"[\w.-]+", s) for s in (owner, repo)):
            raise ValueError("Invalid repository URL")
        api = f"https://api.github.com/repos/{owner}/{repo}"
        info = json.loads(fetch_public(api)[0])
        commit = json.loads(
            fetch_public(api + "/commits/" + quote(info["default_branch"], safe=""))[0]
        )["sha"]
        readme = json.loads(fetch_public(api + f"/readme?ref={commit}")[0])
        if readme.get("encoding") != "base64":
            raise ValueError("Unsupported GitHub README encoding")
        body = base64.b64decode(readme["content"]).decode("utf-8")
        source = f"github:{owner.casefold()}/{repo.casefold()}:{readme['path']}"
        return {
            "source_key": source,
            "source_type": "github_readme",
            "title": f"{owner}/{repo} — README",
            "url": f"https://github.com/{owner}/{repo}/blob/{commit}/{readme['path']}",
            "text": body,
            "metadata": {"commit_sha": commit, "path": readme["path"], "language": "unknown"},
        }
    body, final_url, content_type = fetch_public(canonical)
    if "html" in content_type:
        text = trafilatura.extract(body.decode("utf-8", errors="replace"), include_links=True)
    elif any(kind in content_type for kind in ("text/plain", "text/markdown")):
        text = body.decode("utf-8", errors="replace")
    else:
        raise ValueError("Only HTML, text, and Markdown sources are supported")
    if not text:
        raise ValueError("No readable content found")
    return {
        "source_key": f"web:{canonical}",
        "source_type": "web_page",
        "title": final_url,
        "url": final_url,
        "text": text,
        "metadata": {"language": "unknown", "final_url": final_url},
    }
