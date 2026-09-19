#!/usr/bin/env python3
"""Serve FaithMap over HTTPS (and HTTP fallback) for Mac + phone LAN."""
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import os
import signal
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent
DEFAULT_PORT = 8887
HTTP_PORT = 8888
CERT = ROOT / ".local-cert.pem"
KEY = ROOT / ".local-key.pem"


class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True
    request_queue_size = 128


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    timeout = 15

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        path = (self.path or "/").split("?", 1)[0]
        if path == "/data/places.json":
            gz = ROOT / "data" / "places.json.gz"
            accept = self.headers.get("Accept-Encoding") or ""
            if gz.exists() and "gzip" in accept:
                data = gz.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
        if path == "/geo/counties.geojson":
            gz = ROOT / "geo" / "counties.geojson.gz"
            accept = self.headers.get("Accept-Encoding") or ""
            if gz.exists() and "gzip" in accept:
                data = gz.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
        if path != "/" and not (ROOT / path.lstrip("/")).exists():
            if not path.startswith("/api"):
                self.path = "/index.html"
        return super().do_GET()

    def guess_type(self, path):
        p = (path or "").split("?", 1)[0].lower()
        if p.endswith(".js") or p.endswith(".mjs"):
            return "text/javascript"
        return super().guess_type(path)

    def end_headers(self):
        path = (self.path or "").split("?", 1)[0].lower()
        if path.endswith(
            (".png", ".jpg", ".jpeg", ".webp", ".ico", ".webmanifest")
        ) or path.endswith("manifest.webmanifest"):
            self.send_header("Cache-Control", "public, max-age=86400")
        else:
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def log_message(self, fmt, *args):
        tag = "https" if getattr(self.server, "is_https", False) else "http"
        sys.stderr.write("%s %s - %s\n" % (self.address_string() and tag, self.address_string(), fmt % args))
        sys.stderr.flush()


def _lan_ips():
    ips = []

    def add(ip):
        if (
            ip
            and ip not in ips
            and not ip.startswith("127.")
            and not ip.startswith("169.254.")
        ):
            ips.append(ip)

    for iface in ("en0", "en1", "en2", "bridge0"):
        try:
            ip = subprocess.check_output(
                ["ipconfig", "getifaddr", iface],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            add(ip)
        except Exception:
            pass
    try:
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            add(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        pass
    return ips


def _local_hostnames():
    names = ["localhost"]
    try:
        n = subprocess.check_output(
            ["scutil", "--get", "LocalHostName"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if n:
            names.append(n)
            names.append(n + ".local")
    except Exception:
        pass
    return names


def _cert_text():
    if not CERT.exists():
        return ""
    try:
        return subprocess.check_output(
            ["openssl", "x509", "-in", str(CERT), "-noout", "-text"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return ""


def _ensure_cert(lan_ips, hostnames):
    text = _cert_text()
    needed = list(hostnames) + ["127.0.0.1"] + list(lan_ips)
    missing = [n for n in needed if n not in text]
    if CERT.exists() and KEY.exists() and not missing:
        return False

    dns_lines = ["DNS.%d = %s" % (i, n) for i, n in enumerate(hostnames, start=1)]
    ip_lines = ["IP.1 = 127.0.0.1"]
    for i, ip in enumerate(lan_ips, start=2):
        ip_lines.append("IP.%d = %s" % (i, ip))
    cfg = "\n".join(
        [
            "[req]",
            "distinguished_name = dn",
            "x509_extensions = v3_req",
            "prompt = no",
            "[dn]",
            "CN = localhost",
            "[v3_req]",
            "subjectAltName = @alt",
            "basicConstraints = CA:FALSE",
            "keyUsage = digitalSignature, keyEncipherment",
            "extendedKeyUsage = serverAuth",
            "[alt]",
            *dns_lines,
            *ip_lines,
            "",
        ]
    )
    with tempfile.NamedTemporaryFile("w", suffix=".cnf", delete=False) as f:
        f.write(cfg)
        cfg_path = f.name
    try:
        subprocess.check_call(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(KEY),
                "-out",
                str(CERT),
                "-days",
                "825",
                "-config",
                cfg_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    finally:
        Path(cfg_path).unlink(missing_ok=True)
    return True


def _free_ports(*ports):
    for port in ports:
        try:
            out = subprocess.check_output(
                ["lsof", "-nP", "-ti", "TCP:%d" % port, "-sTCP:LISTEN"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).split()
        except Exception:
            continue
        for pid in out:
            if not pid.isdigit() or int(pid) == os.getpid():
                continue
            try:
                os.kill(int(pid), signal.SIGTERM)
                print("  reclaimed port %d from pid %s" % (port, pid), flush=True)
            except Exception:
                pass
    time.sleep(1)


def _run_http():
    http = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    http.is_https = False
    http.serve_forever()


def main():
    argv = [a for a in sys.argv[1:] if a]
    port = DEFAULT_PORT
    if argv:
        port = int(argv[0])

    _free_ports(port, HTTP_PORT)

    lan = _lan_ips()
    hosts = _local_hostnames()
    lan_hint = lan[0] if lan else "<this-mac-ip>"
    bonjour = next((h for h in hosts if h.endswith(".local")), None)

    _ensure_cert(lan, hosts)
    if not CERT.exists() or not KEY.exists():
        print("Could not create .local-cert.pem / .local-key.pem", file=sys.stderr)
        sys.exit(1)

    threading.Thread(target=_run_http, daemon=True).start()

    httpsd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    httpsd.is_https = True
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(CERT), keyfile=str(KEY))
    httpsd.socket = ctx.wrap_socket(httpsd.socket, server_side=True)

    print("FaithMap", flush=True)
    print("  HTTP:   http://%s:%s/" % (lan_hint, HTTP_PORT), flush=True)
    print("  HTTPS:  https://%s:%s/" % (lan_hint, port), flush=True)
    if bonjour:
        print("          https://%s:%s/" % (bonjour, port), flush=True)
    print("  Mac:    https://127.0.0.1:%s/" % port, flush=True)
    print("  Same Wi-Fi. On phone: Advanced → Proceed for the self-signed cert.", flush=True)
    try:
        httpsd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)


if __name__ == "__main__":
    main()
