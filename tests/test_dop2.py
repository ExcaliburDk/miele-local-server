"""Tests for DOP2 binary parser and annotators (incl. DOP2FileList self fix)."""

from miele_local.dop2 import MieleAttribute, MieleAttributeParser
from miele_local.dop2_structures import DOP2FileList, DOP2NotificationShow


def test_empty_and_short_dop2_payloads_do_not_crash():
    parser = MieleAttributeParser()
    assert parser.parse_bytes(b"") == []
    assert parser.parse_bytes(b"\x00\x01\x02") == []


def test_dop2_filelist_annotator_accepts_self():
    tree = {
        1: MieleAttribute(8, "string", b"test.bin"),
        2: MieleAttribute(4, "string", b"\xaa\xbb\xcc\xdd"),
        3: MieleAttribute(4, "string", b"desc"),
        4: MieleAttribute(1, "U8", 1),
    }
    ann = DOP2FileList(tree)
    ann.readFields()
    data = ann.to_json_dict()
    assert data["fileName"] == "test.bin"
    assert data["sha256"] == "aa bb cc dd"


def test_dop2_notification_show_json_safe():
    tree = {1: MieleAttribute(2, "E16", 42)}
    ann = DOP2NotificationShow(tree)
    ann.readFields()
    assert ann.to_json_dict()["messageId"] == "42"
