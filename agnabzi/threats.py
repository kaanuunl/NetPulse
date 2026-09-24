"""Heuristic security indicators computed from local network activity.

These are *risk signals*, not verdicts. They surface behaviour that is common
to unwanted or malicious software — running from a temporary folder while
reaching the internet, exposing a remote-access service to the whole network,
fanning out to very many hosts at once, or using a port associated with
remote-access trojans — so a person can investigate. They analyse only this
computer's own connections and complement, never replace, an antivirus product.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Folders that malware commonly stages itself in. A signed installer may also
# sit in Downloads, so that case is only a medium signal.
HIGH_RISK_DIRS = ("\\temp\\", "\\windows\\temp\\", "\\appdata\\local\\temp\\", "$recycle.bin\\")
MEDIUM_RISK_DIRS = ("\\downloads\\", "\\users\\public\\")

# Services that are a real exposure when they listen on every interface, i.e.
# are reachable from other devices on the network rather than only locally.
SENSITIVE_LISTEN_PORTS = {
    23: "Telnet", 445: "SMB", 3389: "RDP", 5900: "VNC", 5985: "WinRM", 5986: "WinRM",
    22: "SSH", 3306: "MySQL", 5432: "PostgreSQL", 6379: "Redis", 27017: "MongoDB",
}
HIGH_EXPOSURE_PORTS = {23, 445, 3389, 5900, 5985, 5986}

# Ports frequently used by remote-access trojans and backdoors. Legitimate uses
# exist, so this is only ever a low-severity prompt to look closer.
RAT_PORTS = {1337, 4444, 4445, 5555, 6666, 12345, 12346, 27374, 31337, 54321}

FANOUT_THRESHOLD = 45
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def location_risk(exe: str) -> str | None:
    if not exe:
        return None
    path = exe.lower()
    if any(marker in path for marker in HIGH_RISK_DIRS):
        return "high"
    if any(marker in path for marker in MEDIUM_RISK_DIRS):
        return "medium"
    return None


def location_category(exe: str) -> str:
    path = exe.lower()
    if "$recycle.bin\\" in path:
        return "recyclebin"
    if "\\temp\\" in path or "\\appdata\\local\\temp\\" in path or "\\windows\\temp\\" in path:
        return "temp"
    if "\\downloads\\" in path:
        return "downloads"
    if "\\users\\public\\" in path:
        return "public"
    return "unknown"


@dataclass(frozen=True)
class AppActivity:
    key: str
    name: str
    exe: str
    has_publisher: bool
    internet_ips: frozenset[str] = frozenset()
    internet_ports: frozenset[int] = frozenset()
    exposed_services: dict[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Finding:
    kind: str
    severity: str
    app: str
    key: str
    exe: str
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.key}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "severity": self.severity,
            "app": self.app, "key": self.key, "exe": self.exe, "data": self.data,
        }


def analyze(activities: list[AppActivity]) -> list[Finding]:
    findings: list[Finding] = []
    for app in activities:
        risk = location_risk(app.exe)
        if risk and app.internet_ips:
            findings.append(Finding(
                "suspicious_location", risk, app.name, app.key, app.exe,
                {"category": location_category(app.exe), "unknown_publisher": not app.has_publisher,
                 "hosts": len(app.internet_ips)},
            ))
        if app.exposed_services:
            severity = "high" if any(p in HIGH_EXPOSURE_PORTS for p in app.exposed_services) else "medium"
            findings.append(Finding(
                "exposed_service", severity, app.name, app.key, app.exe,
                {"services": [{"port": p, "name": n} for p, n in sorted(app.exposed_services.items())]},
            ))
        if len(app.internet_ips) >= FANOUT_THRESHOLD:
            findings.append(Finding(
                "host_fanout", "medium", app.name, app.key, app.exe, {"hosts": len(app.internet_ips)},
            ))
        if app.internet_ips:
            rat = sorted(app.internet_ports & RAT_PORTS)
            if rat:
                findings.append(Finding("unusual_port", "low", app.name, app.key, app.exe, {"ports": rat}))
    findings.sort(key=lambda f: (SEVERITY_RANK[f.severity], f.app.lower()))
    return findings


def summarize(findings: list[Finding]) -> dict[str, Any]:
    counts = {"high": 0, "medium": 0, "low": 0}
    for finding in findings:
        counts[finding.severity] += 1
    if counts["high"]:
        level = "bad"
    elif counts["medium"]:
        level = "warn"
    elif counts["low"]:
        level = "low"
    else:
        level = "good"
    return {"level": level, "counts": counts, "total": len(findings)}
