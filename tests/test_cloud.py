"""Tests for Miele Cloud OAuth (PKCE) and GroupKeyId extraction helpers."""

from miele_local.cloud import (
    build_authorize_url,
    parse_redirect_url,
    pick_household,
    region_candidates,
)


def test_build_authorize_url_pkce_and_dk():
    url, ch = build_authorize_url("dk")
    assert "prod.map.miele-iot.com/dk/authorize" in url
    assert "miele%3A%2F%2Foauth2-code%2F" in url
    assert "code_challenge_method=S256" in url
    assert ch.cc == "dk"
    assert len(ch.verifier) >= 43
    assert len(ch.state) >= 16


def test_parse_redirect_url_extracts_code_and_verifies_state():
    code, state = parse_redirect_url(
        "miele://oauth2-code/?code=abc123&state=xyz789", expected_state="xyz789"
    )
    assert code == "abc123"
    assert state == "xyz789"

    bare_code, _ = parse_redirect_url("abc123", expected_state="xyz789")
    assert bare_code == "abc123"

    console_line = (
        "Failed to launch 'miele://oauth2-code/?code=con999&state=xyz789' "
        "because the scheme does not have a registered handler."
    )
    c_code, c_state = parse_redirect_url(console_line, expected_state="xyz789")
    assert c_code == "con999"
    assert c_state == "xyz789"


def test_pick_household_prefers_matching_or_nonempty():
    groups = [
        {"groupId": "EMPTY00000000001", "groupKey": "A" * 128, "devices": []},
        {"groupId": "LIVE000000000002", "groupKey": "B" * 128, "devices": [{"id": "1"}]},
    ]
    assert pick_household(groups)["groupId"] == "LIVE000000000002"
    assert pick_household(groups, {"EMPTY00000000001"})["groupId"] == "EMPTY00000000001"
    assert region_candidates("dk") == ["EU", "AS"]
    assert region_candidates("au") == ["AS", "EU"]
