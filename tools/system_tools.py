import asyncio
import ipaddress
import logging
import platform
import shutil
import socket
from typing import Any, Dict, Optional

import httpx
from langchain_core.tools import tool

try:
    import psutil
except ImportError:
    psutil = None

logger = logging.getLogger(__name__)


class NetworkClient:
    def __init__(self, timeout: int = 15):
        self.client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        self.client.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; AgentBot/1.0)",
            }
        )

    async def fetch_json(self, url: str) -> Dict[str, Any]:
        response = await self.client.get(url)
        response.raise_for_status()
        return response.json()

    async def my_ip(self) -> Dict[str, Any]:
        try:
            return await self.fetch_json("https://api.ip.sb/jsonip")
        except Exception:
            return await self.fetch_json("https://api.ipify.org?format=json")

    async def get_ip_info(self, ip: str) -> Dict[str, Any]:
        ipaddress.ip_address(ip)
        return await self.fetch_json(f"https://api.ip.sb/geoip/{ip}")

    async def aclose(self):
        await self.client.aclose()


_net_client: Optional[NetworkClient] = None


def get_net_client() -> NetworkClient:
    global _net_client
    if _net_client is None:
        _net_client = NetworkClient()
    return _net_client


def _format_result(data: Dict[str, Any]) -> str:
    return "\n".join(f"- {key}: {value}" for key, value in data.items())


def _render_network_error(exc: Exception) -> str:
    return f"Error: {exc}"


@tool("get_public_ip")
async def get_public_ip() -> str:
    """Gets public IPv4/IPv6 address and ISP info."""
    try:
        return _format_result(await get_net_client().my_ip())
    except Exception as e:
        return _render_network_error(e)


@tool("lookup_ip_info")
async def lookup_ip_info(ip: str) -> str:
    """Looks up geolocation, ASN, and ISP details for a given IP address."""
    try:
        return _format_result(await get_net_client().get_ip_info(ip))
    except Exception as e:
        return _render_network_error(e)


def _get_system_info_sync() -> str:
    try:
        uname = platform.uname()
        total, _, free = shutil.disk_usage(".")
        info = [
            f"OS: {uname.system} {uname.release}",
            f"Machine: {uname.machine}",
            f"Disk: {free // (2**30)}GB free / {total // (2**30)}GB total",
        ]
        if psutil:
            ram = psutil.virtual_memory()
            cpu_load = psutil.cpu_percent(interval=0.1)
            info.append(f"RAM: {ram.percent}% used ({round(ram.total / 1024**3, 1)}GB total)")
            info.append(f"CPU: {cpu_load}% load ({psutil.cpu_count()} cores)")
        else:
            info.append("(psutil not installed, detailed RAM/CPU info unavailable)")
        return "\n".join(info)
    except Exception as e:
        return f"Error: {e}"


@tool("get_system_info")
async def get_system_info() -> str:
    """Returns real-time OS, CPU, RAM and Disk usage statistics."""
    return await asyncio.to_thread(_get_system_info_sync)


def _get_local_network_info_sync() -> str:
    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        lines = [f"Hostname: {hostname}", f"Local IP: {local_ip}", "\nInterfaces:"]
        if psutil:
            for interface, addrs in psutil.net_if_addrs().items():
                ips = [addr.address for addr in addrs if addr.family == socket.AF_INET]
                if ips:
                    lines.append(f" - {interface}: {', '.join(ips)}")
        return "\n".join(lines)
    except Exception as e:
        return str(e)


@tool("get_local_network_info")
async def get_local_network_info() -> str:
    """Lists local IP addresses, hostnames, and active network interfaces."""
    return await asyncio.to_thread(_get_local_network_info_sync)
