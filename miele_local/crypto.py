"""Miele@home cryptographic transport, async HTTP client, and commissioning helpers."""

import asyncio
import binascii
import hashlib
import hmac
import json
import logging
import secrets
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import aiohttp
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .dop2 import MieleAttributeParser
from .dop2_structures import DOP2Annotators

logger = logging.getLogger(__name__)

# Miele's HTTP framing requires this exact Content-Type string in both HMAC and header.
MIELE_CONTENT_TYPE = "application / vnd.miele.v1 + json; charset = utf - 8"
MIELE_ACCEPT = "application/vnd.miele.v1+json"
MIELE_USER_AGENT = "Miele@mobile 2.3.3 iOS"
MIELE_DATE_HEADER = "Thu, 01 Jan 1970 02:09:22 GMT"


class MieleLocalError(Exception):
    """Base error for Miele local protocol operations."""

    def __init__(self, message: str, host: str = "", status_code: int = 502):
        super().__init__(message)
        self.error = message
        self.device = host
        self.status_code = status_code

    def asdict(self) -> Dict[str, Any]:
        return {"error": self.error, "device": self.device}


class MieleDOP2UnavailableError(MieleLocalError):
    """Raised when a device does not expose or drops the DOP2 endpoint."""

    def __init__(self, message: str, host: str = ""):
        super().__init__(message, host=host, status_code=404)


@dataclass
class MieleProvisioningInfo:
    """GroupID (8 bytes hex) + GroupKey (64 bytes hex = 32-byte AES key + 64-byte HMAC key)."""

    groupid: str
    groupkey_hex: str

    def __post_init__(self) -> None:
        cleaned_id = self.groupid.strip().upper()
        cleaned_key = self.groupkey_hex.strip().upper()
        self.groupkey = bytearray.fromhex(cleaned_key)
        if len(bytes.fromhex(cleaned_id)) != 8:
            raise ValueError("GroupID must be 16 hex characters (8 bytes)")
        if len(self.groupkey) != 64:
            raise ValueError("GroupKey must be 128 hex characters (64 bytes)")
        self.groupid = cleaned_id
        self.groupkey_hex = self.groupkey.hex().upper()

    def get_aes_key(self) -> bytes:
        return bytes(self.groupkey[0:32])

    def get_signature_key(self) -> bytes:
        return bytes(self.groupkey)

    @staticmethod
    def generate_random() -> "MieleProvisioningInfo":
        return MieleProvisioningInfo(
            groupid=secrets.token_hex(8),
            groupkey_hex=secrets.token_hex(64),
        )

    def to_dict(self) -> Dict[str, str]:
        return {"GroupID": self.groupid, "GroupKey": self.groupkey_hex}

    def to_pairing_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=4)

    @staticmethod
    def from_pairing_json(payload: str) -> "MieleProvisioningInfo":
        data = json.loads(payload)
        return MieleProvisioningInfo(
            groupid=data["GroupID"],
            groupkey_hex=data["GroupKey"],
        )

    def __str__(self) -> str:
        return f"groupId={self.groupid}"


class MieleCryptoProvider:
    """Handles MieleH256 request signing, AES-256-CBC body encryption/decryption, and async HTTP."""

    def __init__(
        self,
        provisioning_info: MieleProvisioningInfo,
        request_timeout: float = 8.0,
        dop2_throttle_seconds: float = 0.05,
    ) -> None:
        self.provisioningInfo = provisioning_info
        self.request_timeout = request_timeout
        self.dop2_throttle_seconds = dop2_throttle_seconds

    @staticmethod
    def iv_from_signature(signature: str) -> bytes:
        signature_bytes = bytearray.fromhex(signature)
        return bytes(signature_bytes[0:16])

    def iv_from_auth_header(self, auth_header: str) -> bytes:
        if ":" not in auth_header:
            raise ValueError(f"Malformed MieleH256 header: {auth_header}")
        _, response_signature = auth_header.split(":", 1)
        return self.iv_from_signature(response_signature.strip())

    @staticmethod
    def decrypt_bytes(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        return decryptor.update(ciphertext) + decryptor.finalize()

    @staticmethod
    def encrypt_bytes(plaintext: bytes, key: bytes, iv: bytes) -> bytes:
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        encryptor = cipher.encryptor()
        return encryptor.update(plaintext) + encryptor.finalize()

    def decrypt_response_bytes(self, content: bytes, headers: Dict[str, str]) -> bytes:
        # Case-insensitive header lookup for aiohttp CIMultiDictProxy and plain dicts
        auth_header = None
        for k, v in headers.items():
            if k.lower() == "x-signature":
                auth_header = v
                break
        if not auth_header:
            raise MieleLocalError("Missing X-Signature header in encrypted response")
        response_iv = self.iv_from_auth_header(auth_header)
        return self.decrypt_bytes(content, self.provisioningInfo.get_aes_key(), response_iv)

    def sign(
        self,
        http_method: str,
        host: str,
        resource_path: str,
        content_type_header: str,
        accept_header: str,
        date: str,
        body: Union[str, bytes],
    ) -> str:
        body_bytes = body.encode("utf-8") if isinstance(body, str) else body
        header_block = (
            "\n".join(
                [
                    http_method,
                    f"{host}/{resource_path}",
                    content_type_header,
                    accept_header,
                    date,
                ]
            )
            + "\n"
        )
        payload_bytes = header_block.encode("utf-8") + body_bytes
        digest = hmac.new(
            self.provisioningInfo.get_signature_key(),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest().upper()
        return digest

    def get_headers_with_auth(
        self,
        http_method: str,
        host: str,
        resource_path: str,
        content_type_header: str,
        accept_header: str,
        date: str,
        body: Union[str, bytes],
    ) -> Tuple[Dict[str, str], str]:
        signature = self.sign(
            http_method,
            host,
            resource_path,
            content_type_header,
            accept_header,
            date,
            body,
        )
        headers = {
            "Content-Type": content_type_header,
            "Host": host,
            "User-Agent": MIELE_USER_AGENT,
            "Authorization": f"MieleH256 {self.provisioningInfo.groupid}:{signature}",
            "Date": date,
            "Accept": accept_header,
        }
        return headers, signature

    @staticmethod
    def pad_body_bytes(payload: bytes) -> bytes:
        blocksize = 16
        remainder = len(payload) % blocksize
        if remainder == 0:
            return payload
        padding = blocksize - remainder
        return payload.ljust(len(payload) + padding, b"\x20")

    @staticmethod
    def pad_body_str(payload: str) -> str:
        """Pad JSON string payload to >=64 bytes AND a multiple of 16 bytes for AES-CBC.

        Fixes upstream MieleCrypto bug where payloads longer than 64 bytes were not
        16-byte aligned and caused AES-CBC ValueError.
        """
        if len(payload) == 0:
            return ""
        if payload[-1] != "}":
            raise ValueError("Plaintext JSON payload must terminate with '}'")
        raw = payload.encode("utf-8")
        target_len = max(64, ((len(raw) + 15) // 16) * 16)
        pad_count = target_len - len(raw)
        if pad_count == 0:
            return payload
        return payload[:-1] + (" " * pad_count) + "}"

    def encrypt_payload(self, payload: Union[str, bytes], iv: bytes) -> bytes:
        raw = payload.encode("utf-8") if isinstance(payload, str) else payload
        return self.encrypt_bytes(raw, self.provisioningInfo.get_aes_key(), iv)

    def prepare_request(
        self,
        http_method: str,
        host: str,
        resource_path: str,
        payload: Union[str, bytes] = "",
    ) -> Tuple[str, Dict[str, str], Optional[bytes]]:
        method = http_method.strip().upper()
        clean_path = resource_path.lstrip("/")
        if isinstance(payload, str):
            prepared_payload: Union[str, bytes] = self.pad_body_str(payload)
        else:
            prepared_payload = self.pad_body_bytes(payload)

        headers, signature = self.get_headers_with_auth(
            method,
            host,
            clean_path,
            MIELE_CONTENT_TYPE,
            MIELE_ACCEPT,
            MIELE_DATE_HEADER,
            prepared_payload,
        )
        body_encrypted: Optional[bytes] = None
        if len(prepared_payload) > 0:
            iv = self.iv_from_signature(signature)
            body_encrypted = self.encrypt_payload(prepared_payload, iv)

        url = f"http://{host}/{clean_path}"
        return url, headers, body_encrypted

    async def send_http_request(
        self,
        http_method: str,
        host: str,
        resource_path: str,
        payload: Union[str, bytes] = "",
        session: Optional[aiohttp.ClientSession] = None,
        timeout: Optional[float] = None,
    ) -> Tuple[bytes, int, Dict[str, str]]:
        """Send an encrypted Miele@home HTTP request asynchronously with explicit timeouts."""
        url, headers, body_encrypted = self.prepare_request(
            http_method, host, resource_path, payload
        )
        req_timeout = aiohttp.ClientTimeout(total=timeout or self.request_timeout)
        owns_session = session is None
        if owns_session:
            session = aiohttp.ClientSession(timeout=req_timeout)

        try:
            async with session.request(
                http_method.strip().upper(),
                url,
                headers=headers,
                data=body_encrypted,
                timeout=req_timeout,
            ) as resp:
                raw_content = await resp.read()
                resp_headers = dict(resp.headers)
                status = resp.status

            if status == 200:
                if not raw_content:
                    return b"", status, resp_headers
                decrypted = self.decrypt_response_bytes(raw_content, resp_headers)
                return decrypted, status, resp_headers
            if status == 204:
                return b"", status, resp_headers
            if status in (401, 403):
                raise MieleLocalError(
                    f"Authentication rejected by Miele appliance (HTTP {status}). Check GroupID/GroupKey.",
                    host=host,
                    status_code=status,
                )
            if status == 404:
                raise MieleLocalError(
                    f"Resource not found on Miele appliance: /{resource_path.lstrip('/')} (HTTP 404)",
                    host=host,
                    status_code=404,
                )
            raise MieleLocalError(
                f"Miele appliance returned HTTP {status} for /{resource_path.lstrip('/')}",
                host=host,
                status_code=502,
            )
        except MieleLocalError:
            raise
        except asyncio.TimeoutError as exc:
            raise MieleLocalError(
                f"Request to {host}/{resource_path.lstrip('/')} timed out after {timeout or self.request_timeout}s",
                host=host,
                status_code=504,
            ) from exc
        except aiohttp.ClientError as exc:
            raise MieleLocalError(
                f"Communication error with {host}: {exc}",
                host=host,
                status_code=502,
            ) from exc
        finally:
            if owns_session:
                await session.close()

    async def read_dop2_node(
        self,
        host: str,
        device_route: str,
        node: str = "",
        session: Optional[aiohttp.ClientSession] = None,
        timeout: float = 6.0,
    ) -> Any:
        resource_path = f"Devices/{device_route}/DOP2/{node}" if node != "" else f"Devices/{device_route}/DOP2/"
        try:
            decrypted, status, _ = await self.send_http_request(
                "GET", host, resource_path, session=session, timeout=timeout
            )
        except MieleLocalError as exc:
            raise MieleDOP2UnavailableError(
                f"DOP2 Root Node not found ({exc.error})", host=host
            ) from exc

        if not decrypted or status == 204:
            raise MieleDOP2UnavailableError("DOP2 endpoint returned empty response", host=host)
        try:
            return json.loads(decrypted.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise MieleDOP2UnavailableError(
                f"DOP2 endpoint did not return valid JSON: {exc}", host=host
            ) from exc

    async def read_dop2_leaf(
        self,
        host: str,
        node: Union[int, str],
        device_route: str,
        leaf: Union[int, str],
        idx1: int = 0,
        idx2: int = 0,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> Tuple[List[Any], bytes]:
        resource_path = f"Devices/{device_route}/DOP2/{node}/{leaf}?idx1={idx1}&idx2={idx2}"
        decrypted, _, _ = await self.send_http_request(
            "GET", host, resource_path, session=session, timeout=6.0
        )
        if not decrypted:
            return [], b""
        parser = MieleAttributeParser()
        return parser.parse_bytes(decrypted), decrypted

    async def write_dop2_leaf(
        self,
        host: str,
        device_route: str,
        unit: int,
        attribute: int,
        payload: Union[str, bytes],
        idx1: int = 0,
        idx2: int = 0,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> Tuple[List[Any], bytes]:
        resource_path = f"Devices/{device_route}/DOP2/{unit}/{attribute}?idx1={idx1}&idx2={idx2}"
        decrypted, _, _ = await self.send_http_request(
            "PUT", host, resource_path, payload=payload, session=session, timeout=6.0
        )
        if not decrypted:
            return [], b""
        parser = MieleAttributeParser()
        return parser.parse_bytes(decrypted), decrypted

    async def read_dop2_recursive(
        self,
        host: str,
        device_route: str,
        max_duration: float = 25.0,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> Dict[str, Any]:
        """Walk the DOP2 tree with throttling and bounded total duration (fixes Issues #1 and #29)."""
        owns_session = session is None
        if owns_session:
            session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6.0))

        loop = asyncio.get_running_loop()
        deadline = loop.time() + max_duration

        try:
            root_node = await self.read_dop2_node(host, device_route, session=session)
            if not isinstance(root_node, (list, dict)):
                raise MieleDOP2UnavailableError("DOP2 Root Node not found", host=host)

            node_ids = list(root_node.keys()) if isinstance(root_node, dict) else list(root_node)
            dop_tree_decoded: Dict[str, Any] = {}
            dop_tree_binary: Dict[str, Any] = {}
            dop_tree_annotated: Dict[str, Any] = {}
            truncated = False

            for node_id in node_ids:
                if loop.time() >= deadline:
                    truncated = True
                    break
                node_key = str(node_id)
                dop_tree_decoded[node_key] = {}
                dop_tree_binary[node_key] = {}
                dop_tree_annotated[node_key] = {}

                if self.dop2_throttle_seconds > 0:
                    await asyncio.sleep(self.dop2_throttle_seconds)

                try:
                    leaves = await self.read_dop2_node(
                        host, device_route, node=node_key, session=session
                    )
                except Exception as exc:
                    dop_tree_decoded[node_key] = f"Error reading node {node_key}: {exc}"
                    continue

                if not isinstance(leaves, (list, set, tuple)):
                    continue

                for leaf_id in sorted(set(leaves), key=lambda x: str(x)):
                    if loop.time() >= deadline:
                        truncated = True
                        break
                    leaf_key = str(leaf_id)
                    if self.dop2_throttle_seconds > 0:
                        await asyncio.sleep(self.dop2_throttle_seconds)

                    try:
                        leaf_fields, leaf_bytes = await self.read_dop2_leaf(
                            host, node_key, device_route, leaf_key, 0, 0, session=session
                        )
                        dop_tree_binary[node_key][leaf_key] = (
                            binascii.hexlify(leaf_bytes).decode("ascii") if leaf_bytes else ""
                        )
                        field_map: Dict[int, Any] = {}
                        field_str_map: Dict[str, str] = {}
                        for idx, field_data in enumerate(leaf_fields, start=1):
                            field_map[idx] = field_data
                            field_str_map[str(idx)] = str(field_data)
                        dop_tree_decoded[node_key][leaf_key] = field_str_map

                        for annotator_cls in DOP2Annotators:
                            if annotator_cls.getLeaf() == [int(node_key), int(leaf_key)]:
                                try:
                                    inst = annotator_cls(field_map)
                                    inst.readFields()
                                    dop_tree_annotated[node_key][annotator_cls.__name__] = (
                                        inst.to_json_dict()
                                    )
                                except Exception:
                                    pass
                    except Exception as exc:
                        dop_tree_decoded[node_key][leaf_key] = (
                            f"Error reading node {node_key}, leaf {leaf_key}: {exc}"
                        )

            return {
                "dopTreeAnnotated": dop_tree_annotated,
                "dopTreeDecoded": dop_tree_decoded,
                "dopTreeBinary": dop_tree_binary,
                "truncated": truncated,
            }
        finally:
            if owns_session:
                await session.close()


async def provision_keys_on_device(
    host: str,
    provisioning_info: Optional[MieleProvisioningInfo] = None,
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """Generate (or reuse) keys and upload them via HTTP + HTTPS /Security/Commissioning."""
    from email.utils import formatdate

    info = provisioning_info or MieleProvisioningInfo.generate_random()
    payload = info.to_pairing_json()
    attempts: List[Dict[str, Any]] = []
    client_timeout = aiohttp.ClientTimeout(connect=3.0, total=timeout)
    date_hdr = formatdate(usegmt=True)
    common_headers = {
        "Date": date_hdr,
        "Authorization": "MielePairing:Pairing",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        candidates = [
            (f"http://{host}/Security/Commissioning", common_headers, False),
            (f"https://{host}/Security/Commissioning", common_headers, False),
        ]
        for url, headers, ssl_verify in candidates:
            try:
                async with session.put(
                    url,
                    data=payload,
                    headers=headers,
                    ssl=ssl_verify,
                ) as resp:
                    body = (await resp.text()).strip()[:300]
                    attempts.append({"url": url, "status": resp.status, "response": body})
                    if 200 <= resp.status < 300:
                        return {
                            "success": True,
                            "provisioning_info": info.to_dict(),
                            "attempts": attempts,
                        }
            except Exception as exc:
                attempts.append({"url": url, "status": None, "error": str(exc)})

    forbidden = any(a.get("status") == 403 for a in attempts)
    unreachable = all(a.get("status") is None for a in attempts)
    if forbidden:
        error_title = f"Appliance at {host} rejected commissioning (HTTP 403 Forbidden)"
        steps_to_fix = [
            "Temporarily allow the appliance internet access if it is on a blocked IoT VLAN.",
            (
                "Add the appliance in the official Miele mobile app, then open its settings "
                "and choose 'Remove device'."
            ),
            "Ensure the appliance is powered on and awake, then retry commissioning here.",
        ]
        hint = (
            "Device returned HTTP 403 Forbidden. Link the appliance temporarily in the official "
            "Miele app and choose 'Remove device' to clear the factory commissioning lock, then retry."
        )
    elif unreachable:
        error_title = f"Could not reach appliance at {host}"
        steps_to_fix = [
            "Verify the IP address is correct and the appliance is connected to your Wi-Fi network.",
            "Make sure the appliance is powered on (not completely unplugged/asleep) and reachable from this server.",
            "If the appliance is not on Wi-Fi yet, use the 'Wi-Fi Setup (SoftAP)' tab first.",
        ]
        hint = f"No response from {host} on port 80 or 443. Check IP address and network connectivity."
    else:
        error_title = f"Commissioning failed on {host}"
        steps_to_fix = [
            "Check that the appliance is awake and in Miele@home pairing mode if required by your model.",
            "Retry commissioning or inspect the technical attempt log below.",
        ]
        hint = error_title

    return {
        "success": False,
        "error_title": error_title,
        "steps_to_fix": steps_to_fix,
        "provisioning_info": info.to_dict(),
        "attempts": attempts,
        "hint": hint,
    }


async def provision_wifi_on_ap(
    ap_host: str,
    ssid: str,
    security: str,
    wifi_key: str = "",
    timeout: float = 8.0,
) -> Dict[str, Any]:
    """Push target Wi-Fi credentials to a Miele appliance while in SoftAP mode."""
    sec_norm = (security or "").strip() or "WPA2"
    is_open = sec_norm.lower() in {"open", "none", "--", ""}
    payload = json.dumps(
        {
            "SSID": ssid,
            "Sec": sec_norm,
            "Key": "" if is_open else wifi_key,
        }
    )
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    attempts: List[Dict[str, Any]] = []
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        for scheme in ("http", "https"):
            url = f"{scheme}://{ap_host}/WLAN"
            try:
                async with session.put(
                    url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    ssl=False,
                ) as resp:
                    text = (await resp.text()).strip()[:300]
                    attempts.append({"url": url, "status": resp.status, "response": text})
                    if 200 <= resp.status < 300:
                        return {"success": True, "attempts": attempts}
            except Exception as exc:
                attempts.append({"url": url, "status": None, "error": str(exc)})
    return {"success": False, "attempts": attempts}
