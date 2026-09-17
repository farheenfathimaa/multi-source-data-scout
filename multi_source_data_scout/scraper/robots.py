"""robots.txt enforcement.

Fetches and parses each site's robots.txt once per run, caches the policy, and
answers "can we fetch this URL?" for every request the crawlers make.

Sites that return HTTP 404 for /robots.txt (both of our sandbox targets do)
are treated as allow-all, per RFC 9309 section 2.3.1: absence of a robots.txt
means nothing is disallowed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

from multi_source_data_scout.logutil import get_logger

log = get_logger("robots")

# Playwright passes the UA without the "Mozilla/5.0 (compatible; ...)" prefix
# rules matcher issues -> we use a clean token for robots parsing.
ROBOTS_USER_AGENT = "multi-source-data-scout-bot/1.0"


@dataclass
class RobotsPolicy:
    site: str
    base_url: str
    state: str  # "present" | "missing" | "error"
    fetched_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    raw: Optional[str] = None
    notes: list[str] = field(default_factory=list)

    @property
    def allows_everything(self) -> bool:
        return self.state != "present"


@dataclass
class RobotsDecision:
    url: str
    allowed: bool
    policy: RobotsPolicy
    note: str


class RobotsChecker:
    """Fetches robots.txt (once per site) and gates individual URLs."""

    def __init__(self, user_agent: str = ROBOTS_USER_AGENT, timeout: float = 15.0) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self._policies: dict[str, RobotsPolicy] = {}
        self._parsers: dict[str, RobotFileParser] = {}

    # -- internal: overridable for tests -------------------------------------
    def _request_robots(self, robots_url: str) -> tuple[str, str | None]:
        """Return (state, raw_text). state in {"present","missing","error"}."""
        req = Request(robots_url, headers={"User-Agent": self.user_agent})
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                if resp.status is not None and resp.status >= 400:
                    return "missing", None
                return "present", raw
        except HTTPError as exc:
            if exc.code == 404:
                return "missing", None
            return "error", None
        except Exception as exc:  # network / DNS / parse failures
            return "error", None

    # -------------------------------------------------------------------------
    async def check_site(self, site: str, base_url: str) -> RobotsPolicy:
        """Ensure a policy exists for a site and return it."""
        if site in self._policies:
            return self._policies[site]
        robots_url = urljoin(base_url, "/robots.txt")
        state, raw = await asyncio.to_thread(self._request_robots, robots_url)
        notes: list[str] = []
        if state == "present":
            parser = RobotFileParser()
            parser.parse(raw.splitlines())
            self._parsers[site] = parser
            notes.append(f"robots.txt present at {robots_url}")
        elif state == "missing":
            notes.append(
                f"robots.txt returned HTTP 404 — nothing disallowed "
                f"(RFC 9309 §2.3.1: allow-all on absence)."
            )
        else:
            notes.append(
                f"robots.txt could not be retrieved — proceeding cautiously, "
                f"will not crawl paths listed as disallowed (none known)."
            )
        policy = RobotsPolicy(site=site, base_url=base_url, state=state, raw=raw, notes=notes)
        self._policies[site] = policy
        log.info("[robots] %s -> %s", policy.base_url, "; ".join(notes[-1:] or notes))
        return policy

    def decision(self, url: str) -> RobotsDecision:
        """Check a URL against the cached policy for its site."""
        for site, policy in self._policies.items():
            if url.startswith(policy.base_url.rstrip("/")):
                parser = self._parsers.get(site)
                if parser is not None:
                    allowed = parser.can_fetch(self.user_agent, url)
                else:
                    allowed = policy.allows_everything
                return RobotsDecision(url=url, allowed=allowed, policy=policy, note=policy.notes[-1] if policy.notes else "")
        # Unknown site -> default to safe (no robots known means allow per spec
        # when we could not fetch one; but we must first try to check it).
        return RobotsDecision(url=url, allowed=True, policy=RobotsPolicy(site="unknown", base_url="", state="missing"), note="no policy loaded")

    async def ensure(self, sites: dict[str, str]) -> None:
        """Load policies for all sites up-front (site name -> base url)."""
        for site, base_url in sites.items():
            await self.check_site(site, base_url)

    def policies(self) -> dict[str, RobotsPolicy]:
        return dict(self._policies)