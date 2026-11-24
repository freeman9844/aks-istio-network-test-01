"""TN14 로컬 관측: 로컬(실행자 PC)에서 API 서버 FQDN 을 해석하고 443 연결을 시도한다."""
import socket


def probe(host: str, port: int = 443, timeout: float = 5.0) -> dict:
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        return {"ip": "", "connected": False}
    ip = infos[0][4][0] if infos else ""
    if not ip:
        return {"ip": "", "connected": False}
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return {"ip": ip, "connected": True}
    except OSError:
        return {"ip": ip, "connected": False}
