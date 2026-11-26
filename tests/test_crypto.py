"""Tests for Miele cryptography, padding, and HMAC-SHA256 signing."""

import json
import pytest

from miele_local.crypto import MieleCryptoProvider, MieleProvisioningInfo


def test_provisioning_round_trip():
    info = MieleProvisioningInfo.generate_random()
    payload = info.to_pairing_json()
    restored = MieleProvisioningInfo.from_pairing_json(payload)
    assert restored.groupid == info.groupid
    assert restored.groupkey_hex == info.groupkey_hex
    assert len(restored.get_aes_key()) == 32
    assert len(restored.get_signature_key()) == 64


def test_invalid_key_lengths():
    with pytest.raises(ValueError):
        MieleProvisioningInfo("1234", "00" * 64)
    with pytest.raises(ValueError):
        MieleProvisioningInfo("00" * 8, "00" * 16)


def test_aes_cbc_round_trip():
    info = MieleProvisioningInfo.generate_random()
    provider = MieleCryptoProvider(info)
    headers, _ = provider.get_headers_with_auth(
        http_method="PUT",
        host="192.168.1.50",
        resource_path="Devices/000123456789/State",
        content_type_header="application / vnd.miele.v1 + json; charset = utf - 8",
        accept_header="application/vnd.miele.v1+json",
        date="Thu, 01 Jan 1970 02:09:22 GMT",
        body=provider.pad_body_str(json.dumps({"ProcessAction": 1})),
    )
    iv = provider.iv_from_auth_header(headers["Authorization"])
    padded = provider.pad_body_str(json.dumps({"ProcessAction": 1}))
    encrypted = provider.encrypt_payload(padded, iv)
    decrypted = MieleCryptoProvider.decrypt_bytes(encrypted, info.get_aes_key(), iv)
    assert json.loads(decrypted.decode("utf-8")) == {"ProcessAction": 1}


def test_pad_body_str_long_payload_is_16_byte_aligned():
    """Regression test: payloads > 64 bytes must still align to 16-byte AES-CBC blocks."""
    long_payload = json.dumps(
        {"SSID": "VeryLongHomeNetworkName1234567890", "Sec": "WPA2", "Key": "super-secret-passphrase-12345"}
    )
    assert len(long_payload) > 64
    padded = MieleCryptoProvider.pad_body_str(long_payload)
    assert len(padded.encode("utf-8")) % 16 == 0
    assert padded.endswith("}")
    assert json.loads(padded) == json.loads(long_payload)
