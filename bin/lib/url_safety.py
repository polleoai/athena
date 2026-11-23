"""url_safety — refuse URLs that point at the local machine or a private network.

Athena fetches URLs it did not choose: iframe embeds, canonical links and
destination tweets found INSIDE captured pages are queued to
inbox/url-new.txt and fetched on the next ingest (#242). Without this check, a
page that embeds `http://169.254.169.254/...` or `http://localhost:8080/admin`
turns Athena into a request forwarder into the user's own network (SSRF), and
writes the response into the vault. Security rules 11 and 12 forbid fetching
localhost or private IPs; this module is the one place that decides it.

Used at two kinds of choke point:
  - producers: canonical_source.queue_canonical_urls and embed_discovery drop
    internal URLs before they reach the inbox;
  - fetchers: capture.main, unified_ingest.ingest and ingest_url.main refuse
    them, whoever put them in the inbox.

internal_url_reason is name-and-literal only, no DNS lookup: queueing and
lint must stay offline, and a lookup then says nothing about fetch time.

Fetch time (#243) is covered by the two fetch primitives below, which every
fetcher of a page-controlled URL uses instead of urllib/curl directly:
  - safe_urlopen: urllib with every connection resolved, checked and pinned
    to a checked address (DNS rebinding), and every redirect target checked
    before it is followed;
  - curl_hops: the same for curl, which does its own DNS and its own -L — a
    hop loop that pins each request with --connect-to and follows redirects
    itself.
Residual: with an HTTP(S) proxy configured the proxy resolves the name, so
only the up-front check applies.

arcus (urllib, Playwright, yt-dlp) fetches on its own; since arcus 0.7.1 it
takes a caller policy for every hop and resolved address, and
install_arcus_guard gives it this module's (arcus_url_policy + safe_urlopen).
"""
from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import urllib.error
import urllib.request
from urllib.parse import unquote, urlsplit

_INTERNAL_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})
_INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa", ".lan", ".intranet")
# Digits, hex and dots only: forms like 2130706433, 0x7f.1, 0177.0.0.1, 127.1
# that ipaddress rejects but the C resolver (and so urllib/curl) accepts.
_LEGACY_IPV4_RE = re.compile(r"^(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+)){0,3}$")
_AMBIGUOUS_RE = re.compile(r"[\\\s\x00-\x1f\x7f]")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def has_ambiguous_chars(url: str) -> bool:
    """True if `url` holds a backslash, whitespace or control character —
    characters parsers disagree on (urllib silently strips tab/CR/LF)."""
    return bool(_AMBIGUOUS_RE.search(url or ""))


def _ip_is_internal(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped or ip.sixtofour
        # NAT64 (RFC 6052 well-known prefix 64:ff9b::/96) embeds an IPv4
        # address in the low 32 bits; ipaddress calls 64:ff9b::7f00:1 global.
        if mapped is None and ip in _NAT64:
            mapped = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if mapped is not None:
            return _ip_is_internal(mapped)
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
        or ip.is_multicast or ip.is_unspecified or not ip.is_global
    )


def _parse_ip(host: str):
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if _LEGACY_IPV4_RE.match(host):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return None
    return None


def internal_url_reason(url: str) -> str:
    """Return why `url` must not be fetched, or "" if it may be.

    Refuses: non-http(s) schemes, a missing host, internal names (localhost,
    *.local, *.internal, ...), and IP literals that are not globally routable
    (loopback, RFC 1918, link-local incl. cloud metadata 169.254.169.254, CGNAT,
    reserved, multicast, unspecified, and IPv4-mapped IPv6 forms of those).
    """
    url = (url or "").strip()
    # Parser differentials: urllib, curl, Chromium and arcus disagree on
    # backslashes, embedded whitespace / control characters and stray '@' in
    # the authority, so a URL this check reads as public can reach an internal
    # host in the fetcher. No legitimate capture URL needs them; refuse.
    if _AMBIGUOUS_RE.search(url):
        return "URL contains a backslash, whitespace or control character"
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        return "unparseable URL"
    if parts.scheme.lower() not in ("http", "https"):
        return f"scheme {parts.scheme or '(none)'!r} is not http(s)"
    try:
        parts.port  # raises on a non-numeric or out-of-range port
    except ValueError:
        return "invalid port"
    if "@" in parts.netloc:
        return "URL carries userinfo (user@host); capture URLs never need it"
    # Percent-encoding in the host is decoded differently by each fetcher
    # (%2e, %09, %40 ...). Only an IPv6 zone id ([fe80::1%25en0]) uses it.
    if "%" in host and not parts.netloc.lstrip().startswith("["):
        return "percent-encoded host"
    host = unquote(host).rstrip(".").lower()
    if not host:
        return "no host"
    # Check the name the resolver will look up: IDNA maps fullwidth digits,
    # ideographic dots and the like to ASCII (１２７。０。０。１ → 127.0.0.1).
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii").rstrip(".").lower()
        except UnicodeError:
            return f"host {host!r} is not a valid international domain name"
    if host in _INTERNAL_NAMES or host.endswith(_INTERNAL_SUFFIXES):
        return f"host {host!r} is a local name"
    ip = _parse_ip(host.split("%", 1)[0])
    if ip is not None and _ip_is_internal(ip):
        return f"host {host!r} is a non-public address"
    return ""


def is_internal_url(url: str) -> bool:
    """True if `url` points at the local machine or a private network."""
    return bool(internal_url_reason(url))


# ── Fetch time (#243) ─────────────────────────────────────────────────────────
MAX_REDIRECTS = 10


class UnsafeURLError(urllib.error.URLError):
    """A fetch was refused because it would reach a non-public address.

    A URLError (so an OSError), so every fetcher's existing soft-fail
    `except URLError / OSError` handles it like any other network failure.
    """


def _address_is_internal(ip) -> bool:
    """The address check used at connect time (one name for tests to patch)."""
    return _ip_is_internal(ip)


def resolve_public(host: str, port: int) -> list[tuple]:
    """Resolve `host` and return its getaddrinfo entries, or raise
    UnsafeURLError if ANY address is non-public. All-or-nothing: a rebinding
    server answering with one public and one private address is refused, not
    raced."""
    try:
        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise urllib.error.URLError(e) from e
    if not infos:
        raise urllib.error.URLError(f"{host}: no addresses")
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        if _address_is_internal(ip):
            raise UnsafeURLError(f"refusing to fetch: {host} resolves to non-public address {ip}")
    return infos


def resolved_url_reason(url: str) -> str:
    """internal_url_reason plus a DNS check, for handing a URL to a fetcher
    Athena does not control (arcus, yt-dlp): "" if it may be fetched.

    Catches the static case — a name that always resolves to a private
    address — but not a redirect or a rebind inside that fetcher; those need
    the fetcher's own checks (arcus). A lookup failure is not a refusal: the
    fetcher fails on its own.
    """
    reason = internal_url_reason(url)
    if reason:
        return reason
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    try:
        resolve_public(parts.hostname, port)
    except UnsafeURLError as e:
        return str(e.reason)
    except (urllib.error.URLError, OSError, UnicodeError):
        return ""
    return ""


def arcus_url_policy(url: str) -> None:
    """Athena's URL policy in arcus's guard form: return None to allow, RAISE
    to refuse. arcus treats any non-None return as a refusal, so this is not
    is_internal_url. arcus calls it for every URL and redirect hop it is about
    to fetch, and with scheme://<ip>:<port>/ for every address a name resolved
    to (it connects only to an address this allowed), so a name-and-literal
    check is enough here and stays offline."""
    reason = internal_url_reason(url)
    if reason:
        raise UnsafeURLError(f"refusing to fetch {url}: {reason}")


def install_arcus_guard() -> None:
    """Make every arcus fetch in this process go through arcus_url_policy,
    with safe_urlopen (pinned, redirect-checked) as arcus's urllib opener.

    Process-wide (set_url_guard), not arcus's thread-scoped using_url_guard:
    scopes are not inherited by threads or pool workers. Idempotent; called by
    each adapter (arcus_html, arcus_file, arcus_video) before it calls arcus.
    An arcus without the guard API (< 0.7.1) raises ImportError, so nothing
    is fetched unguarded."""
    try:
        from arcus.provider_runtime import set_url_guard
    except ImportError as e:
        raise ImportError(
            "arcus-provider-runtime >= 0.7.1 is required (URL guard): "
            'pip install --user "arcus-provider-runtime[html,pdf,office]==0.8.2"') from e
    set_url_guard(arcus_url_policy, urlopen=safe_urlopen)


def _proxy_hosts() -> set[tuple[str, int]]:
    """(host, port) of every proxy configured in the environment."""
    out = set()
    for scheme, proxy in urllib.request.getproxies().items():
        if scheme == "no":
            continue
        p = urlsplit(proxy if "://" in proxy else "http://" + proxy)
        if p.hostname:
            out.add((p.hostname.lower(), p.port or (443 if p.scheme == "https" else 80)))
    return out


def _pinned_create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                              source_address=None, *a, **k):
    """Drop-in for socket.create_connection that connects only to checked
    addresses of the name — the name is never handed to the OS resolver
    again, so it cannot rebind between check and connect. The one exception
    is the user's own configured proxy (often on localhost); the target
    behind it is checked by safe_urlopen and the redirect handler instead."""
    host, port = address
    if (str(host).lower(), int(port)) in _proxy_hosts():
        return socket.create_connection((host, port), timeout, source_address)
    err = None
    for fam, _type, _proto, _canon, sa in resolve_public(host, port):
        try:
            return socket.create_connection((sa[0], port), timeout, source_address)
        except OSError as e:  # try the next checked address
            err = e
    raise err if err else urllib.error.URLError(f"{host}: could not connect")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._create_connection = _pinned_create_connection


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    # TLS still verifies against self.host (server_hostname), so pinning the
    # address does not weaken certificate checks.
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._create_connection = _pinned_create_connection


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PinnedHTTPConnection, req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PinnedHTTPSConnection, req, context=self._context)


class _CheckedRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        reason = internal_url_reason(newurl)
        if reason:
            raise UnsafeURLError(f"refusing to follow redirect to {newurl}: {reason}")
        _check_proxied_target(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _check_proxied_target(url: str) -> None:
    """A proxied request's target is resolved by the proxy, not pinned by
    us; at least refuse a name that resolves to a private address here.
    (An unproxied target is checked and pinned at connect time.)"""
    if _proxy_for(url):
        parts = urlsplit(url)
        resolve_public(parts.hostname, parts.port or (443 if parts.scheme.lower() == "https" else 80))


def _proxy_for(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    scheme = urlsplit(url).scheme.lower()
    return bool(urllib.request.getproxies().get(scheme)) and not urllib.request.proxy_bypass(host)


def safe_urlopen(req, timeout=30, context=None):
    """urllib.request.urlopen for URLs Athena did not choose.

    Refuses (UnsafeURLError) a URL internal_url_reason rejects, any redirect
    target it rejects, and any connection whose name resolves to a non-public
    address — the connection goes to the checked address, never to a fresh
    lookup. With a proxy configured for the URL, the proxy does the DNS, so
    only the up-front name/literal check plus one resolve check apply.
    """
    if isinstance(req, str):
        req = urllib.request.Request(req)
    url = req.full_url
    reason = internal_url_reason(url)
    if reason:
        raise UnsafeURLError(f"refusing to fetch {url}: {reason}")
    _check_proxied_target(url)
    # Pinned handlers in every case (build_opener drops its default
    # HTTP/HTTPS handlers when subclasses are given); the env ProxyHandler
    # decides per request, and the pinned connection lets only the proxy's
    # own address through unchecked.
    opener = urllib.request.build_opener(
        _CheckedRedirectHandler(), urllib.request.ProxyHandler(),
        _PinnedHTTPHandler(), _PinnedHTTPSHandler(context=context))
    return opener.open(req, timeout=timeout)


# ── curl ──────────────────────────────────────────────────────────────────────
_SHORT_FLAGS_RE = re.compile(r"^-[A-Za-z]+$")
_VALUE_FLAGS = ("-A", "-o", "-H", "-e", "-u", "-d", "-D", "-w")
_REDIRECT_CODES = ("301", "302", "303", "307", "308")


def _redirect_target(header_file: str, url: str) -> str:
    """The Location of a 3xx in curl's -D dump (last response block), made
    absolute against `url`; "" when the response is not a redirect."""
    from urllib.parse import urljoin
    try:
        with open(header_file, encoding="latin-1") as fh:
            text = fh.read()
    except OSError:
        return ""
    status, location = "", ""
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("HTTP/"):
            parts = line.split()
            status, location = (parts[1] if len(parts) > 1 else ""), ""
        elif line.lower().startswith("location:"):
            location = line.split(":", 1)[1].strip()
    if status in _REDIRECT_CODES and location:
        return urljoin(url, location)
    return ""


def curl_hops(args: list[str], run, max_redirects: int = MAX_REDIRECTS) -> str:
    """Run curl over `args` (one http(s) URL among them) with the #243 checks.

    `run(cmd)` runs a command and returns a CompletedProcess (capture.py's
    _run). curl resolves names itself and `-L` follows redirects inside one
    process, out of our sight, so instead: each hop checks the URL, resolves
    and checks the name (resolve_public), pins curl to that address with
    --connect-to, runs WITHOUT -L, and reads the redirect target from a -D
    header dump (portable to any curl; -w %{stderr} needs 7.63+). If the
    caller asked for -L the loop follows it after checking it. Returns curl's
    stdout ('' on refusal), with every hop's output concatenated in -I mode as
    `curl -sIL` prints it.
    """
    import os
    import tempfile
    urls = [i for i, a in enumerate(args) if a.startswith(("http://", "https://"))]
    if len(urls) != 1:
        raise ValueError(f"curl_hops needs exactly one URL, got {len(urls)}")
    follow = head_mode = False
    base: list[str] = []
    for i, a in enumerate(args):
        if i == urls[0]:
            continue
        if _SHORT_FLAGS_RE.match(a) and (i == 0 or args[i - 1] not in _VALUE_FLAGS):
            follow = follow or "L" in a
            head_mode = head_mode or "I" in a
            a = "-" + a[1:].replace("L", "")
            if a == "-":
                continue
        elif a == "--location":
            follow = True
            continue
        base.append(a)
    url = args[urls[0]]
    out: list[str] = []
    with tempfile.TemporaryDirectory(prefix="athena-curl-") as td:
        header_file = os.path.join(td, "headers")
        for _hop in range(max_redirects + 1):
            if internal_url_reason(url):
                return ""
            parts = urlsplit(url)
            port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
            try:
                infos = resolve_public(parts.hostname, port)
            except urllib.error.URLError:
                return ""
            ip = str(infos[0][4][0])
            # --connect-to "::IP:PORT" (empty host1/port1 = any): every
            # connection goes to the checked address. --resolve would key
            # on the name, and curl's own spelling of it (IDNA, trailing
            # dot) can differ from ours, letting curl resolve it afresh.
            pin = f"::{'[' + ip + ']' if ':' in ip else ip}:{port}"
            if os.path.exists(header_file):
                os.unlink(header_file)
            cmd = ["curl", *base, "--proto", "=http,https", "--connect-to", pin,
                   "-D", header_file, url]
            stdout = run(cmd).stdout or ""
            nxt = _redirect_target(header_file, url) if follow else ""
            if head_mode:
                out.append(stdout)
            if not nxt:
                return "".join(out) if head_mode else stdout
            url = nxt
    return ""  # redirect cap: treat like curl's "maximum redirects" failure
