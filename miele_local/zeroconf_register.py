"""Minimal AsyncZeroconf-only mDNS registration helper for Miele Local."""

import logging
import socket as _socket
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_reg = None


def _props_to_txt(props: Dict[str, str]):
    return {k: (v.encode("utf-8") if isinstance(v, str) else v) for k, v in props.items()}


def _pack_ipv4(addr: str):
    try:
        return _socket.inet_pton(_socket.AF_INET, addr)
    except Exception:
        return None


def get_primary_ipv4() -> Optional[str]:
    """Return a best-effort primary IPv4 address for this host."""
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        addr = s.getsockname()[0]
        s.close()
        return addr
    except Exception:
        try:
            return _socket.gethostbyname(_socket.gethostname())
        except Exception:
            return None


async def register_service_async(
    name: str = "miele-local",
    port: int = 5001,
    props: Optional[Dict[str, str]] = None,
    service_type: Optional[str] = None,
    advertise_addr: Optional[str] = None,
):
    """Register an HTTP service using AsyncZeroconf."""
    global _reg
    props = props or {}
    service_type = service_type or "_http._tcp.local."

    try:
        from zeroconf import ServiceInfo
        from zeroconf.asyncio import AsyncZeroconf
    except Exception as exc:
        logger.warning("Async zeroconf not available: %s", exc)
        return False, None, f"zeroconf not available: {exc}", "0.0.0.0"

    desc = _props_to_txt(props)
    addr_to_use = advertise_addr or get_primary_ipv4()
    addresses = None
    if addr_to_use:
        packed = _pack_ipv4(addr_to_use)
        if packed:
            addresses = [packed]

    info = ServiceInfo(
        service_type,
        f"{name}.{service_type}",
        addresses=addresses,
        port=port,
        properties=desc,
    )

    try:
        async_zc = AsyncZeroconf()
        await async_zc.async_register_service(info, allow_name_change=True)
        _reg = ("async", async_zc, info)
        logger.info("AsyncZeroconf registered %s on port %s (%s)", name, port, addr_to_use)
        return True, "zeroconf_async", None, addr_to_use or "0.0.0.0"
    except Exception as exc:
        logger.warning("AsyncZeroconf registration failed: %s", exc)
        return False, None, str(exc), "0.0.0.0"


async def unregister_service_async() -> None:
    """Async unregister for the current registration."""
    global _reg
    if not _reg:
        return
    try:
        _, async_zc, info = _reg
        try:
            await async_zc.async_unregister_service(info)
        except Exception:
            pass
        try:
            await async_zc.async_close()
        except Exception:
            pass
    finally:
        _reg = None


def unregister_service() -> None:
    """Best-effort sync unregister wrapper."""
    import asyncio

    global _reg
    if not _reg:
        return
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(unregister_service_async())
        else:
            loop.run_until_complete(unregister_service_async())
    except Exception:
        _reg = None
