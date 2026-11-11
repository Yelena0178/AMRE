"""Bounded metadata-only providers and an offline fixture implementation."""

import json
import os
import re
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Protocol, Self
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree

from filelock import FileLock, Timeout
from pydantic import AwareDatetime, Field, ValidationError, model_validator

from amre.errors import ResearchError
from amre.literature.models import DOI, ArxivID, PaperMetadata, ProviderName
from amre.research.models import Record, Text, VersionedRecord, unique_ids
from amre.research.storage import atomic_write, read_yaml, validate, workspace_path

_ENDPOINTS = {
    ProviderName.ARXIV: "https://export.arxiv.org/api/query",
    ProviderName.CROSSREF: "https://api.crossref.org/works",
    ProviderName.SEMANTIC_SCHOLAR: "https://api.semanticscholar.org/graph/v1/paper",
}
_MIN_INTERVAL = {
    ProviderName.ARXIV: 3,
    ProviderName.CROSSREF: 1,
    ProviderName.SEMANTIC_SCHOLAR: 1,
}
_ATOM = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


class SearchRequest(Record):
    query: Annotated[Text, Field(max_length=240)] | None = None
    doi: DOI | None = None
    arxiv_id: ArxivID | None = None
    limit: Annotated[int, Field(strict=True, ge=1, le=20)] = 5

    @model_validator(mode="after")
    def _search_key(self) -> Self:
        if sum(item is not None for item in (self.query, self.doi, self.arxiv_id)) != 1:
            raise ValueError("provide exactly one keyword query, DOI, or arXiv ID")
        return self


class SearchResult(VersionedRecord):
    provider: ProviderName
    retrieved_at: AwareDatetime
    papers: tuple[PaperMetadata, ...] = ()
    diagnostic: Text | None = None
    cached: bool = False


class NetworkPolicy(VersionedRecord):
    enabled_providers: tuple[ProviderName, ...] = ()
    max_results: Annotated[int, Field(strict=True, ge=1, le=20)] = 10
    timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=30)] = 8
    cache_seconds: Annotated[int, Field(strict=True, ge=0, le=86400 * 30)] = 86400
    minimum_interval_seconds: Annotated[int, Field(strict=True, ge=1, le=30)] = 1

    @model_validator(mode="after")
    def _networks(self) -> Self:
        unique_ids(self.enabled_providers, "network provider")
        if ProviderName.OFFLINE in self.enabled_providers:
            raise ValueError("offline lookup is always available")
        return self


class LiteratureProvider(Protocol):
    def search(self, request: SearchRequest) -> SearchResult: ...


class OfflineFixtureProvider:
    def __init__(self, papers: tuple[PaperMetadata, ...]):
        self.papers = papers

    def search(self, request: SearchRequest) -> SearchResult:
        def matches(paper: PaperMetadata) -> bool:
            if request.doi or request.arxiv_id:
                return bool(
                    request.doi
                    and request.doi == paper.doi
                    or request.arxiv_id
                    and request.arxiv_id == paper.arxiv_id
                )
            text = " ".join((paper.title, *paper.authors)).casefold()
            return all(word in text for word in request.query.casefold().split())

        papers = tuple(paper for paper in self.papers if matches(paper))[: request.limit]
        return SearchResult(
            provider=ProviderName.OFFLINE, retrieved_at=datetime.now(UTC), papers=papers
        )


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _download(url: str, headers: dict[str, str], timeout: int) -> bytes:
    maximum = 512 * 1024
    deadline = time.monotonic() + timeout
    data = bytearray()
    with build_opener(_NoRedirect()).open(Request(url, headers=headers), timeout=timeout) as reply:
        while time.monotonic() < deadline:
            chunk = reply.read1(8192)
            if not chunk:
                return bytes(data)
            data.extend(chunk)
            if len(data) > maximum:
                raise ResearchError("metadata response exceeds byte limit")
    raise TimeoutError("metadata response exceeded its deadline")


def _arxiv_identity(value: str) -> tuple[str, str | None]:
    base = value.strip().split("/abs/")[-1]
    match = re.fullmatch(r"(.+?)v([1-9]\d*)", base)
    return (match[1], "v" + match[2]) if match else (base, None)


def _url(provider: ProviderName, request: SearchRequest, limit: int) -> str:
    base = _ENDPOINTS[provider]
    if provider == ProviderName.ARXIV:
        fields = {"max_results": limit}
        if request.arxiv_id:
            fields["id_list"] = request.arxiv_id
        elif request.doi and request.doi.startswith("10.48550/arxiv."):
            fields["id_list"] = request.doi.removeprefix("10.48550/arxiv.")
        elif request.doi:
            raise ResearchError("Use Crossref or Semantic Scholar for this DOI")
        else:
            fields["search_query"] = f'all:"{request.query.replace(chr(34), " ")}"'
        return f"{base}?{urlencode(fields)}"
    if provider == ProviderName.CROSSREF:
        if request.arxiv_id:
            raise ResearchError("Use arXiv or Semantic Scholar for arXiv IDs")
        if request.doi:
            return f"{base}/{quote(request.doi, safe='')}"
        return base + "?" + urlencode({"query.bibliographic": request.query, "rows": limit})
    fields = {"fields": "title,authors,year,url,externalIds"}
    if request.doi or request.arxiv_id:
        prefix, key = ("DOI", request.doi) if request.doi else ("ARXIV", request.arxiv_id)
        base += f"/{prefix}:{quote(key, safe='')}"
    else:
        base += "/search"
        fields.update(query=request.query, limit=limit)
    return f"{base}?{urlencode(fields)}"


def _atom_rows(data: bytes):
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)", data, flags=re.I):
        raise ResearchError("unsupported XML document declaration")
    root = ElementTree.fromstring(data)
    if root.tag != f"{{{_ATOM['atom']}}}feed":
        raise ResearchError("expected an Atom feed")
    for row in root.findall("atom:entry", _ATOM):

        def get(key, default=None):
            return row.findtext(key, default, namespaces=_ATOM)

        original = get("atom:id", "")
        if "/abs/" not in original:
            raise ResearchError("arXiv returned an error entry")
        arxiv_id, version = _arxiv_identity(original)
        yield dict(
            title=get("atom:title", ""),
            authors=tuple(
                author.findtext("atom:name", "", namespaces=_ATOM)
                for author in row.findall("atom:author", _ATOM)
            ),
            year=int(get("atom:published", "0")[:4]) or None,
            doi=get("arxiv:doi"),
            arxiv_id=arxiv_id,
            version=version,
            source_url="https://arxiv.org/abs/" + arxiv_id + (version or ""),
        )


def _json_rows(provider: ProviderName, data: bytes):
    payload = json.loads(data)
    if provider == ProviderName.CROSSREF:
        message = payload["message"]
        for item in message.get("items", [message]):
            date = next(
                (item[key] for key in ("published", "issued", "created") if key in item), {}
            )
            parts = date.get("date-parts") or [[None]]
            yield dict(
                title=next(iter(item.get("title", [])), ""),
                authors=tuple(
                    " ".join(filter(None, (author.get("given"), author.get("family"))))
                    for author in item.get("author", [])
                ),
                year=(parts[0] or [None])[0],
                doi=item.get("DOI"),
                source_url=item.get("URL") or f"https://doi.org/{item['DOI']}",
            )
    else:
        for item in payload.get("data", [payload]):
            ids = item.get("externalIds") or {}
            arxiv, version = _arxiv_identity(ids["ArXiv"]) if ids.get("ArXiv") else (None, None)
            yield dict(
                title=item.get("title"),
                authors=tuple(author.get("name") for author in item.get("authors", [])),
                year=item.get("year"),
                doi=ids.get("DOI"),
                arxiv_id=arxiv,
                version=version,
                source_url=item.get("url"),
            )


class PublicMetadataProvider:
    def __init__(self, root: Path, provider: ProviderName, *, online: bool):
        self.root = root
        self.name = provider
        self.policy = validate(
            NetworkPolicy,
            read_yaml(workspace_path(root, "research/literature.yaml"), missing_ok=True) or {},
            "literature network policy",
        )
        self.enabled = online and provider in self.policy.enabled_providers

    def search(self, request: SearchRequest) -> SearchResult:
        now = datetime.now(UTC)

        def failure(message: str) -> SearchResult:
            return SearchResult(provider=self.name, retrieved_at=now, diagnostic=message)

        if not self.enabled:
            return failure(
                "UNAVAILABLE: allow this provider in research/literature.yaml and opt in"
            )
        try:
            limit = min(request.limit, self.policy.max_results)
            url = _url(self.name, request, limit)
        except ResearchError as exc:
            return failure(f"UNAVAILABLE: {exc}")
        headers = {"User-Agent": "AMRE literature metadata/1.0"}
        if self.name == ProviderName.SEMANTIC_SCHOLAR and (
            key := os.environ.get("AMRE_SEMANTIC_SCHOLAR_API_KEY")
        ):
            headers["x-api-key"] = key
        directory = workspace_path(self.root, f".amre/literature/{self.name}")
        directory.mkdir(parents=True, exist_ok=True)
        cache = directory / f"{sha256(url.encode()).hexdigest()}.json"
        throttle = directory / "retry-after"
        try:
            with FileLock(directory / "provider.lock", timeout=0):
                if cache.is_file() and time.time() - cache.stat().st_mtime < (
                    self.policy.cache_seconds
                ):
                    cached = SearchResult.model_validate_json(cache.read_bytes())
                    return cached.model_copy(update={"cached": True})
                if throttle.is_file() and time.time() < float(throttle.read_text(encoding="utf-8")):
                    return failure("RATE_LIMITED: retry after provider cooldown")
                interval = max(self.policy.minimum_interval_seconds, _MIN_INTERVAL[self.name])
                atomic_write(throttle, str(time.time() + interval))
                try:
                    data = _download(url, headers, self.policy.timeout_seconds)
                except HTTPError as exc:
                    if exc.code == 429:
                        wait = exc.headers.get("Retry-After", "")
                        atomic_write(
                            throttle, str(time.time() + (int(wait) if wait.isdigit() else 60))
                        )
                    return failure(f"HTTP_{exc.code}: provider refused metadata request")
                rows = (
                    _atom_rows(data)
                    if self.name == ProviderName.ARXIV
                    else _json_rows(self.name, data)
                )
                papers = []
                skipped = 0
                for row in rows:
                    row["title"] = " ".join((row["title"] or "").split())
                    row["authors"] = tuple(name for name in row["authors"] if name)
                    try:
                        papers.append(PaperMetadata(provider=self.name, retrieved_at=now, **row))
                    except ValidationError:
                        skipped += 1
                    if len(papers) >= limit:
                        break
                result = SearchResult(
                    provider=self.name,
                    retrieved_at=now,
                    papers=tuple(papers),
                    diagnostic=f"INCOMPLETE: skipped {skipped} malformed records"
                    if skipped
                    else None,
                )
                atomic_write(cache, result.model_dump_json())
                return result
        except Timeout:
            return failure("RATE_LIMITED: a metadata request is in progress")
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            ElementTree.ParseError,
            ResearchError,
        ):
            return failure("UNAVAILABLE: metadata transport, format or cache failed")
