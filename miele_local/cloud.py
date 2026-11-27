"""Miele Cloud OAuth (PKCE) and household GroupID/GroupKey retrieval.

Allows Miele Local to obtain the exact household GroupID and GroupKey used by
the official Miele mobile app (GET /V2/GroupKeyId/) so both the official app
and local LAN control can work simultaneously without overwriting each other.

Protocol flow adapted from tiehfood/ha-miele-at-lan (MIT License).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import logging
import re
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse

import aiohttp

logger = logging.getLogger(__name__)

CONSUMER_CLIENT_IDS: Dict[str, str] = {
    "ae": "iaXIbUAGv6QTUd8FRNPG5fHN",
    "at": "wNv9HJ3ZcFKH4bxvz0LExQuw",
    "au": "rLXHMAq2VdnICd0eHuefA50w",
    "be": "JFDW4KGg_ppC3kmSOITHNKBp",
    "bg": "v6R8ClqnHRWVYJBlhxah0fiR",
    "br": "zu0ZoLtw2HUj5GH_I_nHfQxM",
    "ca": "-fZUlMeq5FaRsG5UNgtMpr-e",
    "ch": "V52nWiniHyVotglJKplSXnX8",
    "cl": "_BRdHKYtaYRpqk7j67y1mc4W",
    "cn": "_4vxrtrxGJeUNaBd4zTkeSZ2",
    "cy": "9se3MtP42BbyWUIQBaE9c8NE",
    "cz": "npoAzuJP6okjvJ0NqUq9i5Rv",
    "de": "UJgKOxacIul2BcPJAzrQE6p0",
    "dk": "xWgykqRQSa9THqOXWfzZbxsH",
    "ee": "m_4y2qk8ntJbOqu6kx7PYXOB",
    "es": "D0Q4NPBR9dwP2EjX4E0_CtHE",
    "fi": "8JbG7QQsdvEMsYisC_nUfltr",
    "fr": "SOiiE3R4tSD0VxYYBvB8Pi_J",
    "gb": "WigtLzKGJE1Wg6yeZUECV8-P",
    "gr": "meyCOw7Co2ICAx5ZCMb21oiE",
    "hk": "tfbB9jidNn1ahn3_oiTrv8um",
    "hr": "HD4OUUQYAw_5DtVFSe4-rYzR",
    "hu": "2mm2yscHPGJ4tJCVjd6mp-to",
    "ie": "_UU4TA2Iei5eEf0XkYp3ZRKF",
    "in": "5KwIy81GoVqNkxkB20OtTNaf",
    "it": "ARQyaYB0ZxLxJ1SJcjJgctuV",
    "jp": "wk4iaS04nYkN4seEsZbKQ2xB",
    "kr": "_e5CgRBpM9h9ecVoUhpZl9-h",
    "lt": "KzeuROL469pqvGFjSYp2ivQ2",
    "lu": "34UAt-gA9ADx2DWgBbtoQh6X",
    "lv": "MJhu3mAsyk7Tahso0i_7m1ud",
    "mx": "G_FW4x4rXuwJ6v2VFx2REn34",
    "my": "esJDemY095qO2Os6VPhAtky0",
    "nl": "7ItTbQXQ1wthDOue9jvBQ7Iz",
    "no": "411mmVRPOnTNmViGl5aDc5h8",
    "nz": "RtYo_k74PpIFsLBsrcRk9238",
    "pl": "jWbgLScpvIuqjUoYvf1jS-Is",
    "pt": "5ZVD-CuJvpG4YpCO9pQhtrGQ",
    "ro": "AQJyFJC8kgsHvKnC2druvJn1",
    "rs": "qbkOxTdY-dx-0AswysR75wU-",
    "se": "3Mm7m1gD1eU_sUh8yxmShL6S",
    "sg": "x798Jlx93o8YAL3Lg0dDwdzU",
    "si": "UTyhG21RchpI8FPbNeb1vFg1",
    "sk": "pGeafLwcC1_BCLr8DRTCVxSt",
    "th": "kY1LYjhV5Wy8L3Jxe9rh_vYr",
    "tr": "ZSHeNOPc9DcFfqNY42LQAIdl",
    "ua": "bDsz9awSMOt-631KZK2J7dUg",
    "us": "HpsWh2gzgKqRBduPpkZ4Yui9",
    "za": "A0sngQGYP8ZvfZ100eJGZD4M",
}

GIGYA_DC_BY_COUNTRY: Dict[str, str] = {
    "au": "au1",
    "br": "us1",
    "ca": "us1",
    "cl": "us1",
    "hk": "us1",
    "jp": "us1",
    "kr": "us1",
    "mx": "us1",
    "my": "au1",
    "nz": "au1",
    "sg": "au1",
    "th": "au1",
    "us": "us1",
}

REDIRECT_URI = "miele://oauth2-code/"
OAUTH_SCOPE = "openid mcs bpdata zuora"
LOCALES_URL = "https://rest-eu.domestic.miele-iot.com/V3/Locales"
MAP_HOST = "prod.map.miele-iot.com"

REST_HOST_BY_REGION: Dict[str, str] = {
    "EU": "rest-eu.domestic.miele-iot.com",
    "AS": "rest-as.domestic.miele-iot.com",
    "EU2": "rest-eu2.domestic.miele-iot.com",
}

CHALLENGE_TTL_SECONDS = 900.0


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def region_candidates(country: str) -> List[str]:
    dc = GIGYA_DC_BY_COUNTRY.get(country.lower(), "eu1")
    return ["AS", "EU"] if dc == "au1" else ["EU", "AS"]


@dataclass
class PKCEChallenge:
    verifier: str
    challenge: str
    state: str
    nonce: str
    cc: str
    client_id: str
    token_url: Optional[str] = None
    created_at: float = 0.0


@dataclass
class CloudGroupKey:
    group_id: str
    group_key: str
    devices: List[Dict[str, Any]]
    region: str = ""


_OIDC_CACHE: Dict[str, Optional[Tuple[str, str, str]]] = {}
_CHALLENGES: Dict[str, PKCEChallenge] = {}


def _prune_expired_challenges() -> None:
    now = time.time()
    expired = [k for k, ch in _CHALLENGES.items() if (now - ch.created_at) > CHALLENGE_TTL_SECONDS]
    for k in expired:
        _CHALLENGES.pop(k, None)


async def resolve_oidc_override(
    session: aiohttp.ClientSession, cc: str
) -> Optional[Tuple[str, str, str]]:
    """Discover per-country OIDC override (e.g. PL/IT) via /V3/Locales."""
    cc_l = cc.lower()
    if cc_l in _OIDC_CACHE:
        return _OIDC_CACHE[cc_l]

    try:
        async with session.get(
            LOCALES_URL,
            params={"org": cc_l.upper()},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return None
            doc = await resp.json(content_type=None)
    except Exception:
        return None

    if isinstance(doc, list):
        doc = doc[0] if doc else {}
    if not isinstance(doc, dict):
        return None

    endpoints = {
        e.get("type"): e.get("uri")
        for e in ((doc.get("ciam") or {}).get("endpoints") or [])
        if isinstance(e, dict)
    }
    authorize, token = endpoints.get("authorize"), endpoints.get("token")
    if not authorize or not token or urllib.parse.urlparse(authorize).hostname == MAP_HOST:
        _OIDC_CACHE[cc_l] = None
        return None

    client_id: Optional[str] = None
    try:
        async with session.get(
            authorize, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            location = resp.headers.get("Location", "")
        client_id = urllib.parse.parse_qs(
            urllib.parse.urlparse(location).query
        ).get("client_id", [None])[0]
    except Exception:
        pass

    client_id = client_id or CONSUMER_CLIENT_IDS.get(cc_l)
    if not client_id:
        return None
    override = (authorize, token, client_id)
    _OIDC_CACHE[cc_l] = override
    return override


def build_authorize_url(
    cc: str,
    *,
    authorize_url: Optional[str] = None,
    token_url: Optional[str] = None,
    client_id: Optional[str] = None,
) -> Tuple[str, PKCEChallenge]:
    """Build a Miele OAuth PKCE login URL and challenge."""
    cc_l = cc.strip().lower()
    if client_id is None:
        if cc_l not in CONSUMER_CLIENT_IDS:
            raise ValueError(f"Unsupported country code '{cc}'")
        client_id = CONSUMER_CLIENT_IDS[cc_l]

    verifier = _b64url(secrets.token_bytes(64))
    challenge_hash = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = _b64url(secrets.token_bytes(16))
    nonce = _b64url(secrets.token_bytes(16))

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": OAUTH_SCOPE,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge_hash,
        "code_challenge_method": "S256",
    }
    base = authorize_url or f"https://{MAP_HOST}/{cc_l}/authorize"
    url = base + "?" + urllib.parse.urlencode(params)
    ch = PKCEChallenge(
        verifier=verifier,
        challenge=challenge_hash,
        state=state,
        nonce=nonce,
        cc=cc_l,
        client_id=client_id,
        token_url=token_url,
        created_at=time.time(),
    )
    return url, ch


async def start_cloud_oauth(session: aiohttp.ClientSession, country: str = "dk") -> Dict[str, str]:
    """Create and cache a PKCE challenge, returning authorize_url and state."""
    _prune_expired_challenges()
    override = await resolve_oidc_override(session, country)
    if override is None:
        url, ch = build_authorize_url(country)
    else:
        auth_u, tok_u, cid = override
        url, ch = build_authorize_url(country, authorize_url=auth_u, token_url=tok_u, client_id=cid)
    _CHALLENGES[ch.state] = ch
    return {"authorize_url": url, "state": ch.state, "country": ch.cc}


def parse_redirect_url(
    redirect_url: str, expected_state: Optional[str] = None
) -> Tuple[str, Optional[str]]:
    """Extract (code, state) from a miele://oauth2-code/, https redirect, or console error line."""
    raw = redirect_url.strip().strip('"').strip("'")
    match = re.search(r"(?:miele://oauth2-code/|https?://[^\s'\"]+/redirect)\?[^\s'\"]+", raw)
    if match:
        raw = match.group(0)
    if "://" not in raw and "code=" not in raw and "&" not in raw:
        return raw, expected_state

    if "://" in raw:
        qs = urllib.parse.urlparse(raw).query
    else:
        qs = raw.lstrip("?")
    parsed = urllib.parse.parse_qs(qs)
    if "error" in parsed:
        err = parsed["error"][0]
        desc = parsed.get("error_description", [""])[0]
        raise ValueError(f"Miele login returned OAuth error: {err} {desc}".strip())

    code = parsed.get("code", [None])[0]
    if not code:
        raise ValueError(
            "Could not find 'code=' in the pasted redirect URL. Paste the full miele://oauth2-code/?code=... URL."
        )
    state = parsed.get("state", [None])[0]
    if expected_state is not None and state is not None and state != expected_state:
        raise ValueError("Login session state mismatch. Please click 'Open Miele Login' and try again.")
    return code, state


async def exchange_code(
    session: aiohttp.ClientSession, challenge: PKCEChallenge, code: str
) -> Dict[str, Any]:
    """Exchange OAuth authorization code for an access token."""
    url = challenge.token_url or f"https://{MAP_HOST}/{challenge.cc}/token"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": challenge.client_id,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": challenge.verifier,
    }
    async with session.post(url, data=data, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        body = await resp.text()
        try:
            tokens = await resp.json(content_type=None)
        except Exception as exc:
            raise RuntimeError(f"Miele token endpoint returned non-JSON: {body[:200]}") from exc
    if "error" in tokens:
        raise RuntimeError(
            f"Miele token exchange failed: {tokens['error']} {tokens.get('error_description', '')}".strip()
        )
    return tokens


def pick_household(
    groups: List[Dict[str, Any]], prefer_group_ids: Optional[set[str]] = None
) -> Dict[str, Any]:
    """Pick the most relevant household from /V2/GroupKeyId/ results."""
    if prefer_group_ids:
        wanted = {g.upper() for g in prefer_group_ids}
        for grp in groups:
            if (grp.get("groupId") or "").upper() in wanted:
                return grp
    return next((grp for grp in groups if grp.get("devices")), groups[0])


async def fetch_groupkey(
    session: aiohttp.ClientSession,
    access_token: str,
    cc: str = "dk",
    prefer_group_ids: Optional[set[str]] = None,
) -> CloudGroupKey:
    """Query Miele REST /V2/GroupKeyId/ to retrieve household GroupID & GroupKey."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Accept-Language": "de-DE",
        "User-Agent": "MieleLocal/1.0",
    }
    for region in region_candidates(cc):
        host = REST_HOST_BY_REGION[region]
        async with session.get(
            f"https://{host}/V2/GroupKeyId/",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 403:
                raise RuntimeError("Miele account token rejected (missing 'mcs' scope)")
            if resp.status != 200:
                continue
            groups = await resp.json(content_type=None)
        if not groups or not isinstance(groups, list):
            continue
        chosen = pick_household(groups, prefer_group_ids)
        return CloudGroupKey(
            group_id=str(chosen["groupId"]).upper(),
            group_key=str(chosen["groupKey"]).upper(),
            devices=list(chosen.get("devices") or []),
            region=region,
        )
    raise RuntimeError(
        "No Miele household with paired appliances was found on this account."
    )


async def complete_cloud_oauth(
    session: aiohttp.ClientSession,
    redirect_url: str,
    state: Optional[str] = None,
    prefer_group_ids: Optional[set[str]] = None,
) -> CloudGroupKey:
    """Complete OAuth PKCE login and return household GroupID/GroupKey + device list."""
    _prune_expired_challenges()
    code, parsed_state = parse_redirect_url(redirect_url, expected_state=state)
    lookup_state = parsed_state or state

    challenge: Optional[PKCEChallenge] = None
    if lookup_state and lookup_state in _CHALLENGES:
        challenge = _CHALLENGES.pop(lookup_state)
    elif len(_CHALLENGES) == 1 and not lookup_state:
        only_key = next(iter(_CHALLENGES))
        challenge = _CHALLENGES.pop(only_key)

    if challenge is None:
        raise ValueError(
            "Login session expired or not started. Click 'Open Miele Login' first and try again."
        )

    tokens = await exchange_code(session, challenge, code)
    access_token = tokens.get("access_token")
    if not access_token:
        raise RuntimeError("Miele OAuth response did not contain an access_token")

    return await fetch_groupkey(
        session,
        access_token=access_token,
        cc=challenge.cc,
        prefer_group_ids=prefer_group_ids,
    )
