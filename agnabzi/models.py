"""Plain data types shared between the collectors, the storage and the API."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    name: str
    exe: str = ""
    title: str = ""

    @property
    def key(self) -> str:
        return (self.exe or self.name).lower()

    @property
    def display_name(self) -> str:
        return self.title or self.name


@dataclass(frozen=True)
class Connection:
    proto: str
    family: int
    local_ip: str
    local_port: int
    remote_ip: str
    remote_port: int
    status: str
    pid: int

    @property
    def is_listening(self) -> bool:
        return self.status == "LISTEN" or (self.proto == "udp" and not self.remote_ip)


@dataclass
class RemoteStats:
    ip: str
    rx: int = 0
    tx: int = 0
    connections: int = 0
    ports: set[int] = field(default_factory=set)
    last_seen: float = 0.0


@dataclass
class AppStats:
    key: str
    name: str
    exe: str
    title: str
    pids: set[int] = field(default_factory=set)
    rx_total: int = 0
    tx_total: int = 0
    rx_rate: float = 0.0
    tx_rate: float = 0.0
    connections: int = 0
    listening: int = 0
    remotes: dict[str, RemoteStats] = field(default_factory=dict)
    first_seen: float = 0.0
    last_active: float = 0.0

    @property
    def display_name(self) -> str:
        return self.title or self.name

    def remote(self, ip: str) -> RemoteStats:
        stats = self.remotes.get(ip)
        if stats is None:
            stats = self.remotes[ip] = RemoteStats(ip)
        return stats

    def trim_remotes(self, limit: int) -> None:
        if len(self.remotes) <= limit:
            return
        ranked = sorted(self.remotes.values(), key=lambda r: (r.connections > 0, r.rx + r.tx, r.last_seen))
        for stale in ranked[: len(self.remotes) - limit]:
            del self.remotes[stale.ip]
