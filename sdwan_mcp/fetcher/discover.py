"""Discover fragment URLs from the DevNet landing page for a given vManage version.

The DevNet documentation site is an SPA whose nav tree is embedded as an inline
JS object literal assigned to a variable named ``webJson``. Every operation and
schema fragment appears as a ``content:"./<uuid>/(apis|models)/<rest>"`` leaf
inside that literal.

Rather than parse JavaScript, we extract leaves with a single regex over the
raw HTML. This is robust to nav-tree restructuring and we do not need the rest
of the tree for stitching, because each fragment's ``spec.path`` and
``spec.method`` are authoritative (see ``stitch.py``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Captures: "./<uuid>/<kind>/<rest>"  where kind is "apis" or "models"
_CONTENT_LEAF_RE = re.compile(
    r'content:\s*"\./(?P<uuid>[a-f0-9-]{36})/(?P<kind>apis|models)/(?P<rest>[^"]+)"'
)
# Captures the pubhub bucket prefix from any media URL on the page (used as
# fallback in case the SPA moves prefixes again). Pattern is intentionally
# permissive.
_PUBHUB_PREFIX_RE = re.compile(r"pubhub\.devnetcloud\.com/media/(?P<bucket>[a-z0-9-]+)/docs/")

_DEFAULT_BUCKET_TEMPLATE = "cisco-catalyst-sd-wan-api-guide-{slug}"


@dataclass(frozen=True)
class FragmentRef:
    """One operation- or model-fragment to fetch.

    ``url`` is the absolute URL.
    ``kind`` is ``"apis"`` or ``"models"``.
    ``name`` is the file basename without ``.json``, used as the schema name for
    model fragments.
    """

    url: str
    uuid: str
    kind: str
    rest: str  # e.g. "v1/device/{deviceId}/get.json" or "Device.json"

    @property
    def name(self) -> str:
        base = self.rest.rsplit("/", 1)[-1]
        return base.removesuffix(".json")


@dataclass(frozen=True)
class Discovery:
    """Outcome of parsing a DevNet landing page."""

    version: str
    slug: str
    pubhub_bucket: str
    api_fragments: tuple[FragmentRef, ...]
    model_fragments: tuple[FragmentRef, ...]

    @property
    def all_fragments(self) -> tuple[FragmentRef, ...]:
        return self.api_fragments + self.model_fragments


class DiscoveryError(RuntimeError):
    """Raised when the discovery page cannot be parsed."""


def version_to_slug(version: str) -> str:
    """Turn ``20.18`` into ``20-18``."""
    return version.replace(".", "-")


def discovery_url(version: str) -> str:
    """Return the canonical DevNet docs URL for a version."""
    return f"https://developer.cisco.com/docs/sdwan/{version_to_slug(version)}/"


def parse_discovery_html(html: str, version: str) -> Discovery:
    """Extract all fragment URLs from a DevNet landing page.

    Raises ``DiscoveryError`` if no ``apis/`` leaves are found, which is the
    strongest signal that the SPA shape has changed.
    """
    slug = version_to_slug(version)
    bucket = _detect_bucket(html, slug)
    prefix = f"https://pubhub.devnetcloud.com/media/{bucket}/docs"

    seen: set[str] = set()
    api_refs: list[FragmentRef] = []
    model_refs: list[FragmentRef] = []
    for match in _CONTENT_LEAF_RE.finditer(html):
        uuid = match.group("uuid")
        kind = match.group("kind")
        rest = match.group("rest")
        url = f"{prefix}/{uuid}/{kind}/{rest}"
        if url in seen:
            continue
        seen.add(url)
        ref = FragmentRef(url=url, uuid=uuid, kind=kind, rest=rest)
        if kind == "apis":
            api_refs.append(ref)
        else:
            model_refs.append(ref)

    if not api_refs:
        raise DiscoveryError(
            f"No API fragments found on {discovery_url(version)}; "
            "the DevNet SPA shape may have changed."
        )

    return Discovery(
        version=version,
        slug=slug,
        pubhub_bucket=bucket,
        api_fragments=tuple(api_refs),
        model_fragments=tuple(model_refs),
    )


def _detect_bucket(html: str, slug: str) -> str:
    """Detect the pubhub bucket name; fall back to the canonical template."""
    for match in _PUBHUB_PREFIX_RE.finditer(html):
        bucket = match.group("bucket")
        if slug in bucket:
            return bucket
    return _DEFAULT_BUCKET_TEMPLATE.format(slug=slug)
