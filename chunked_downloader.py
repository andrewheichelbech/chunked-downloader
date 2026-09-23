#!/usr/bin/env python3
"""
chunked_downloader.py
======================
Download large files through a size-limited firewall/proxy by fetching them
in sub-2GB HTTP Range chunks, then reassemble the chunks into the original
file. Works on Windows, macOS, and Linux (pure Python + `requests`).

Requirements:
    pip install requests

-----------------------------------------------------------------------------
QUICK START
-----------------------------------------------------------------------------

0) Not sure what chunk size your proxy actually allows? Probe it first:

    python chunked_downloader.py probe "https://.../xdp-sovereign-5.0.0-nifi-v1-part1.tgz"

   This tests real Range requests of increasing size against that URL
   (discarding the data) and recommends a --chunk-size-mb value with a
   safety margin already built in. See the note on MB vs GB below --
   "2GB" is ambiguous, and this probe sidesteps the ambiguity entirely by
   measuring your actual limit rather than assuming one.

1) Download one or more URLs, chunked at ~1.9GB per request (safely under a
   2GB proxy limit), into ./downloads/:

    python chunked_downloader.py download \
        "https://.../xdp-sovereign-5.0.0-nifi-v1-part1.tgz" \
        "https://.../xdp-sovereign-5.0.0-nifi-v1-part2.tgz" \
        -o ./downloads

   Or put the URLs (one per line, '#' comments allowed) in a text file:

    python chunked_downloader.py download --urls-file urls.txt -o ./downloads

   Interrupted mid-way? Just re-run the same command. Fully-downloaded
   chunks are detected by size and skipped; only the incomplete one resumes.

2) After downloading, each URL's chunks are auto-reassembled into a single
   file in the output directory (e.g. xdp-sovereign-5.0.0-nifi-v1-part1.tgz).
   The tool also prints whether each reassembled file starts with the gzip
   magic bytes (1f 8b). That tells you which of these two situations you're
   in:

     - Every reassembled file IS a valid standalone gzip/tar.gz
       -> extract each one separately (step 3a)

     - Only the FIRST reassembled file is a valid gzip stream, and the
       rest are NOT (no gzip magic bytes)
       -> the vendor split one big .tar.gz into raw byte fragments
          (e.g. with the Unix `split` command) BEFORE upload, unrelated
          to our own chunking here. You must concatenate them back into
          one file, in the correct order, before extracting (step 3b).

3a) Extract a self-contained archive:

    python chunked_downloader.py extract ./downloads/xdp-soverign-v2-release-5.0.0-nifi-v1.tgz -o ./extracted

3b) If the part1..partN files are raw fragments of ONE archive, combine them
    in order first, then extract:

    python chunked_downloader.py combine \
        ./downloads/xdp-sovereign-5.0.0-nifi-v1-part1.tgz \
        ./downloads/xdp-sovereign-5.0.0-nifi-v1-part2.tgz \
        ./downloads/xdp-sovereign-5.0.0-nifi-v1-part3.tgz \
        ./downloads/xdp-sovereign-5.0.0-nifi-v1-part4.tgz \
        ./downloads/xdp-sovereign-5.0.0-nifi-v1-part5.tgz \
        ./downloads/xdp-sovereign-5.0.0-nifi-v1-part6.tgz \
        -o ./downloads/xdp-sovereign-5.0.0-nifi-v1-combined.tgz

    python chunked_downloader.py extract ./downloads/xdp-sovereign-5.0.0-nifi-v1-combined.tgz -o ./extracted

-----------------------------------------------------------------------------
NOTES
-----------------------------------------------------------------------------
- Chunk size is controlled with --chunk-size-mb (default 1900). IMPORTANT --
  units: this multiplies by 1,048,576 (1024*1024, i.e. MiB, the same
  definition your OS file manager uses for "MB"), NOT the decimal
  1,000,000-byte definition some proxy vendors use for "GB" in their docs.
  Concretely: --chunk-size-mb 1900 = 1900 * 1,048,576 = 1,992,294,400 bytes.
  If your proxy's limit is a strict DECIMAL 2GB (2,000,000,000 bytes), that
  default leaves only about 7MB of headroom -- uncomfortably tight. If the
  limit is 2 GiB (2^31 = 2,147,483,648 bytes), the same default has about
  155MB of headroom. Since you can't always tell which definition your
  proxy vendor means, don't guess: run `probe` (see QUICK START step 0)
  against a real URL to measure the actual working limit and get a
  recommended --chunk-size-mb with a safety margin already applied.
- Chunks are stored under downloads/<filename>.chunks/ and are only deleted
  after the tool confirms the reassembled file's size matches the server's
  reported Content-Length. Use --keep-chunks to keep them anyway.
- The script verifies total byte count after reassembly. It does not
  independently verify a checksum unless the server provides an ETag for
  the *whole* object (multipart-uploaded S3 objects often have composite
  ETags that aren't plain MD5s, so this is reported as informational only,
  not a hard failure).
- All network calls use a session with retries and exponential backoff.
"""

import argparse
import hashlib
import os
import sys
import time
import tarfile
from pathlib import Path
from urllib.parse import urlparse, urlsplit, urlunsplit, quote

try:
    import requests
    import urllib3
except ImportError:
    print("ERROR: this script requires the 'requests' package.\n"
          "Install it with:  pip install requests", file=sys.stderr)
    sys.exit(1)

DEFAULT_CHUNK_MB = 1900          # ~1.9 GB per request, under a 2GB cap
MAX_RETRIES = 6
BACKOFF_BASE = 2.0               # seconds; doubles each retry
READ_BLOCK = 1024 * 1024         # 1MB streaming read size
GZIP_MAGIC = b"\x1f\x8b"


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:3.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}PB"


def filename_from_url(url):
    return os.path.basename(urlparse(url).path) or "downloaded.bin"


def make_session():
    s = requests.Session()
    s.headers.update({"User-Agent": "chunked-downloader/1.0"})
    return s


def build_session(args):
    """Build a session honoring proxy / TLS options, if the parsed args have them
    (only 'download' and 'check' subcommands expose these flags)."""
    s = make_session()

    if getattr(args, "no_env_proxy", False):
        s.trust_env = False  # ignore HTTP_PROXY/HTTPS_PROXY/NO_PROXY env vars entirely

    proxy_url = getattr(args, "proxy", None)
    proxy_user = getattr(args, "proxy_user", None)
    proxy_pass = getattr(args, "proxy_pass", None)

    if proxy_url:
        if proxy_user or proxy_pass:
            parsed = urlsplit(proxy_url)
            netloc = f"{quote(proxy_user or '', safe='')}:{quote(proxy_pass or '', safe='')}@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            proxy_url = urlunsplit((parsed.scheme or "http", netloc, parsed.path, parsed.query, parsed.fragment))
        s.proxies.update({"http": proxy_url, "https": proxy_url})
    elif proxy_user or proxy_pass:
        print("ERROR: --proxy-user/--proxy-pass require --proxy to also be set.", file=sys.stderr)
        sys.exit(1)

    proxy_auth = getattr(args, "proxy_auth", None)
    if proxy_auth == "ntlm":
        try:
            from requests_ntlm import HttpNtlmAuth
        except ImportError:
            print("ERROR: --proxy-auth ntlm requires the 'requests_ntlm' package.\n"
                  "Install it with:  pip install requests_ntlm\n"
                  "Note: this authenticates NTLM challenges from the ORIGIN server, not "
                  "all proxies negotiate NTLM the same way through requests/urllib3 -- if "
                  "this doesn't work, use a local proxy shim (see README: cntlm / Px) "
                  "instead.", file=sys.stderr)
            sys.exit(1)
        if not (proxy_user and proxy_pass):
            print("ERROR: --proxy-auth ntlm requires --proxy-user and --proxy-pass "
                  "(user as 'DOMAIN\\\\username' if your environment needs a domain).",
                  file=sys.stderr)
            sys.exit(1)
        s.auth = HttpNtlmAuth(proxy_user, proxy_pass)

    if getattr(args, "insecure", False):
        s.verify = False
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    elif getattr(args, "ca_bundle", None):
        s.verify = args.ca_bundle

    return s


def add_proxy_args(parser):
    g = parser.add_argument_group(
        "proxy/TLS options",
        "Most corporate proxies work automatically via HTTP_PROXY/HTTPS_PROXY "
        "environment variables (recommended, since it keeps credentials out of "
        "your shell history and process list). Use these flags only if you need "
        "to override that."
    )
    g.add_argument("--proxy", help="Proxy URL, e.g. http://proxy.company.com:8080")
    g.add_argument("--proxy-user", help="Proxy username (Basic auth); requires --proxy")
    g.add_argument("--proxy-pass", help="Proxy password (Basic auth); requires --proxy. "
                                         "Prefer setting via environment/keychain over the "
                                         "command line where possible.")
    g.add_argument("--proxy-auth", choices=["basic", "ntlm"], default="basic",
                    help="Proxy auth scheme. 'basic' (default) works with --proxy-user/pass "
                         "embedded credentials. 'ntlm' needs 'pip install requests_ntlm' and "
                         "often still doesn't work for NTLM-at-the-proxy (see README).")
    g.add_argument("--no-env-proxy", action="store_true",
                    help="Ignore HTTP_PROXY/HTTPS_PROXY/NO_PROXY environment variables")
    g.add_argument("--insecure", action="store_true",
                    help="Disable TLS certificate verification (only for proxies that "
                         "intercept HTTPS with a cert you can't easily install -- reduces "
                         "security, use with caution)")
    g.add_argument("--ca-bundle", help="Path to a CA bundle (.pem) to trust, e.g. your "
                                        "corporate proxy's root certificate, instead of "
                                        "disabling verification entirely")


def head_info(session, url):
    """Return (total_size, accepts_ranges, etag)."""
    r = session.head(url, allow_redirects=True, timeout=30)
    r.raise_for_status()
    size = int(r.headers.get("Content-Length", 0))
    accepts_ranges = r.headers.get("Accept-Ranges", "").lower() == "bytes"
    etag = r.headers.get("ETag")
    return size, accepts_ranges, etag


def download_range_with_retry(session, url, start, end, dest_path):
    """Download bytes [start, end] inclusive into dest_path, with retries.
    Resumes within the chunk itself if partially written."""
    expected_len = end - start + 1
    existing = dest_path.stat().st_size if dest_path.exists() else 0

    if existing == expected_len:
        return  # already complete

    if existing > expected_len:
        # Corrupt/oversized partial chunk from a previous bad run; restart it.
        dest_path.unlink()
        existing = 0

    attempt = 0
    while True:
        attempt += 1
        range_start = start + existing
        headers = {"Range": f"bytes={range_start}-{end}"}
        try:
            with session.get(url, headers=headers, stream=True, timeout=60) as r:
                if r.status_code not in (200, 206):
                    raise RuntimeError(f"Unexpected status {r.status_code} for range request")
                mode = "ab" if existing else "wb"
                with open(dest_path, mode) as f:
                    for block in r.iter_content(chunk_size=READ_BLOCK):
                        if block:
                            f.write(block)
            final_size = dest_path.stat().st_size
            if final_size != expected_len:
                raise RuntimeError(
                    f"Chunk size mismatch: expected {expected_len}, got {final_size}"
                )
            return
        except Exception as e:
            if attempt >= MAX_RETRIES:
                raise RuntimeError(
                    f"Failed downloading range {start}-{end} after {attempt} attempts: {e}"
                ) from e
            existing = dest_path.stat().st_size if dest_path.exists() else 0
            wait = BACKOFF_BASE ** attempt
            print(f"    retry {attempt}/{MAX_RETRIES} after error ({e}); "
                  f"waiting {wait:.0f}s, resuming at byte {existing}")
            time.sleep(wait)


def reassemble(chunk_paths, dest_path, expected_size=None):
    with open(dest_path, "wb") as out:
        for cp in chunk_paths:
            with open(cp, "rb") as f:
                while True:
                    block = f.read(READ_BLOCK)
                    if not block:
                        break
                    out.write(block)
    actual = dest_path.stat().st_size
    if expected_size is not None and actual != expected_size:
        raise RuntimeError(
            f"Reassembled file size {actual} != expected {expected_size} for {dest_path}"
        )
    return actual


def looks_like_gzip(path):
    with open(path, "rb") as f:
        return f.read(2) == GZIP_MAGIC


def download_one(session, url, out_dir, chunk_size, keep_chunks):
    fname = filename_from_url(url)
    dest_file = out_dir / fname
    chunk_dir = out_dir / f"{fname}.chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n== {url}")
    if dest_file.exists():
        # Already fully reassembled previously; verify against server size.
        total_size, _, _ = head_info(session, url)
        if dest_file.stat().st_size == total_size:
            print(f"   already downloaded and reassembled ({human(total_size)}), skipping")
            return dest_file
        else:
            print("   existing output file has wrong size; re-downloading")
            dest_file.unlink()

    total_size, accepts_ranges, etag = head_info(session, url)
    print(f"   size: {human(total_size)}  accept-ranges: {accepts_ranges}  etag: {etag}")
    if not accepts_ranges and total_size > chunk_size:
        print("   WARNING: server did not advertise Accept-Ranges: bytes. "
              "Will attempt range requests anyway (S3 normally supports them).")

    # Compute chunk boundaries
    ranges = []
    start = 0
    idx = 0
    while start < total_size:
        end = min(start + chunk_size - 1, total_size - 1)
        ranges.append((idx, start, end))
        start = end + 1
        idx += 1
    if not ranges:  # zero-byte file edge case
        ranges = [(0, 0, -1)]

    chunk_paths = []
    for idx, start, end in ranges:
        chunk_path = chunk_dir / f"part{idx:04d}.chunk"
        chunk_paths.append(chunk_path)
        size_str = human(end - start + 1) if end >= start else "0B"
        print(f"   chunk {idx}: bytes {start}-{end} ({size_str})", end="")
        if chunk_path.exists() and chunk_path.stat().st_size == (end - start + 1):
            print("  [already downloaded]")
            continue
        print("  downloading...")
        download_range_with_retry(session, url, start, end, chunk_path)

    print("   reassembling...")
    actual = reassemble(chunk_paths, dest_file, expected_size=total_size)
    print(f"   done: {dest_file}  ({human(actual)})")

    if looks_like_gzip(dest_file):
        print("   format check: starts with gzip magic bytes -> looks like a "
              "standalone valid .tar.gz")
    else:
        print("   format check: does NOT start with gzip magic bytes -> this is "
              "likely a raw fragment of a larger archive; you'll need to "
              "'combine' it with adjacent part files before extracting.")

    if not keep_chunks:
        for cp in chunk_paths:
            try:
                cp.unlink()
            except OSError:
                pass
        try:
            chunk_dir.rmdir()
        except OSError:
            pass

    return dest_file


def cmd_download(args):
    urls = list(args.urls)
    if args.urls_file:
        with open(args.urls_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)
    if not urls:
        print("No URLs given. Pass them as arguments or via --urls-file.", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    chunk_size = args.chunk_size_mb * 1024 * 1024
    session = build_session(args)

    results = []
    for url in urls:
        try:
            results.append(download_one(session, url, out_dir, chunk_size, args.keep_chunks))
        except Exception as e:
            print(f"   FAILED: {e}", file=sys.stderr)
            results.append(None)

    print("\n== Summary ==")
    ok = sum(1 for r in results if r)
    print(f"{ok}/{len(urls)} files downloaded and reassembled successfully.")
    if ok != len(urls):
        sys.exit(1)


def cmd_combine(args):
    parts = [Path(p) for p in args.parts]
    for p in parts:
        if not p.exists():
            print(f"ERROR: missing file {p}", file=sys.stderr)
            sys.exit(1)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Combining {len(parts)} files into {out_path} (in the order given)...")
    total = reassemble(parts, out_path)
    print(f"Done: {out_path} ({human(total)})")
    if looks_like_gzip(out_path):
        print("format check: combined file starts with gzip magic bytes -> looks valid.")
    else:
        print("format check: combined file does NOT start with gzip magic bytes -> "
              "double-check the part order, or these may not be simple byte fragments.")


def cmd_extract(args):
    src = Path(args.archive)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Extracting {src} -> {out_dir} ...")
    with tarfile.open(src, "r:*") as tf:
        tf.extractall(out_dir)
    print("Extraction complete.")


def cmd_check(args):
    """Quick pre-flight check: can we reach the URL through the configured
    proxy, and does the proxy/server respond the way we expect, WITHOUT
    pulling gigabytes of data."""
    session = build_session(args)
    url = args.url
    print(f"Checking connectivity to: {url}")
    if session.proxies:
        print(f"   via proxy: {session.proxies}")
    elif session.trust_env:
        print("   using proxy from HTTP_PROXY/HTTPS_PROXY environment variables, if set")
    else:
        print("   no proxy configured (direct connection)")
    try:
        r = session.head(url, allow_redirects=True, timeout=30)
    except requests.exceptions.ProxyError as e:
        print(f"FAILED: proxy error: {e}", file=sys.stderr)
        print("   -> If your proxy needs NTLM/Kerberos auth, requests can't negotiate "
              "that directly. Run a local proxy shim like cntlm or Px that handles the "
              "NTLM/Kerberos handshake to the real proxy, then point --proxy at "
              "http://127.0.0.1:<local-port> instead.", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.SSLError as e:
        print(f"FAILED: TLS/SSL error: {e}", file=sys.stderr)
        print("   -> Your proxy may be intercepting HTTPS with its own certificate. "
              "Get that root CA from your IT team and pass it via --ca-bundle, or as a "
              "last resort use --insecure.", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)

    if r.status_code == 407:
        print("FAILED: HTTP 407 Proxy Authentication Required.", file=sys.stderr)
        print("   -> Provide --proxy-user/--proxy-pass, or set credentials directly in "
              "an HTTP_PROXY/HTTPS_PROXY env var (http://user:pass@host:port).",
              file=sys.stderr)
        sys.exit(1)

    r.raise_for_status()
    size = int(r.headers.get("Content-Length", 0))
    print(f"OK: status {r.status_code}, size {human(size)}, "
          f"Accept-Ranges: {r.headers.get('Accept-Ranges', '(not advertised)')}")


def try_fetch_range(session, url, num_bytes, timeout):
    """Attempt to fetch exactly `num_bytes` (bytes 0..num_bytes-1) via a Range
    request, discarding the data as it streams in (nothing is written to
    disk). Returns True if the full amount was received without error,
    False if the connection failed, was reset, or was truncated -- any of
    which indicate this size was rejected somewhere along the path
    (typically a proxy enforcing a size cap)."""
    headers = {"Range": f"bytes=0-{num_bytes - 1}"}
    received = 0
    try:
        with session.get(url, headers=headers, stream=True, timeout=timeout) as r:
            if r.status_code not in (200, 206):
                return False
            for block in r.iter_content(chunk_size=READ_BLOCK):
                received += len(block)
        return received == num_bytes
    except requests.exceptions.RequestException:
        return False


def cmd_probe(args):
    """Empirically find the largest Range request this network path (proxy
    included) will actually deliver in full, by testing increasing sizes
    against the real URL and discarding the data. Recommends a
    --chunk-size-mb value with a safety margin baked in."""
    session = build_session(args)
    url = args.url
    min_bytes = args.min_mb * 1024 * 1024
    max_bytes = args.max_mb * 1024 * 1024
    timeout = args.timeout

    print(f"Probing: {url}")
    try:
        total_size, accepts_ranges, _ = head_info(session, url)
    except requests.exceptions.ProxyError as e:
        print(f"FAILED: proxy error: {e}", file=sys.stderr)
        print("   -> See the 'check' command for proxy/NTLM troubleshooting guidance.",
              file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.SSLError as e:
        print(f"FAILED: TLS/SSL error: {e}", file=sys.stderr)
        print("   -> See --ca-bundle / --insecure options, or the 'check' command.",
              file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"FAILED: could not reach URL: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"   file size: {human(total_size)}  accept-ranges: {accepts_ranges}")
    max_bytes = min(max_bytes, total_size) if total_size else max_bytes
    if max_bytes < min_bytes:
        print(f"ERROR: file is smaller ({human(max_bytes)}) than --min-mb "
              f"({human(min_bytes)}). Nothing to probe.", file=sys.stderr)
        sys.exit(1)

    print(f"   testing sizes between {human(min_bytes)} and {human(max_bytes)} "
          f"(this downloads real data each attempt and is discarded; may take "
          f"a few minutes and use noticeable bandwidth)\n")

    # Phase 1: exponential growth from min_bytes until a request fails, or we
    # hit max_bytes with everything still succeeding.
    last_good = None
    candidate = min_bytes
    first_bad = None
    while candidate <= max_bytes:
        print(f"   trying {candidate // (1024*1024)}MB ...", end=" ", flush=True)
        ok = try_fetch_range(session, url, candidate, timeout)
        print("OK" if ok else "FAILED / truncated")
        if ok:
            last_good = candidate
            if candidate == max_bytes:
                break
            candidate = min(candidate * 2, max_bytes)
        else:
            first_bad = candidate
            break

    if last_good is None:
        print(f"\nEven the minimum tested size ({human(min_bytes)}) failed. "
              f"Try a smaller --min-mb, check connectivity with the 'check' "
              f"command, or verify your proxy settings.", file=sys.stderr)
        sys.exit(1)

    # Phase 2: binary search between last_good and first_bad to narrow down,
    # if we found an upper failure point.
    if first_bad is not None:
        lo, hi = last_good, first_bad
        tolerance = 25 * 1024 * 1024  # stop refining once within 25MB
        while hi - lo > tolerance:
            mid = (lo + hi) // 2
            print(f"   trying {mid // (1024*1024)}MB ...", end=" ", flush=True)
            ok = try_fetch_range(session, url, mid, timeout)
            print("OK" if ok else "FAILED / truncated")
            if ok:
                lo = mid
            else:
                hi = mid
        last_good = lo

    # Recommend a value with a safety margin under the largest size that worked.
    margin = max(int(last_good * 0.05), 25 * 1024 * 1024)  # 5%, at least 25MB
    recommended_bytes = last_good - margin
    recommended_mb = max((recommended_bytes // (1024 * 1024) // 50) * 50, 50)  # round down to nearest 50MB

    print(f"\nLargest size that downloaded successfully: {human(last_good)}")
    if first_bad is None:
        print(f"No failure was found up to --max-mb ({human(max_bytes)}) -- your "
              f"path may allow larger requests than tested. Re-run with a higher "
              f"--max-mb to find the true ceiling, or just use this recommendation.")
    print(f"\nRecommended setting:  --chunk-size-mb {recommended_mb}")
    print(f"(That's {human(recommended_mb * 1024 * 1024)}, "
          f"{recommended_mb * 1024 * 1024:,} bytes -- "
          f"{margin // (1024*1024)}MB safety margin under the largest size that worked.)")


def cmd_checksum(args):
    src = Path(args.file)
    h = hashlib.sha256()
    with open(src, "rb") as f:
        while True:
            block = f.read(READ_BLOCK)
            if not block:
                break
            h.update(block)
    print(f"{h.hexdigest()}  {src}")


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    pd = sub.add_parser("download", help="Download URL(s) in chunks and reassemble")
    pd.add_argument("urls", nargs="*", help="One or more URLs to download")
    pd.add_argument("--urls-file", help="Text file with one URL per line ('#' comments allowed)")
    pd.add_argument("-o", "--output-dir", default="./downloads", help="Where to save files (default: ./downloads)")
    pd.add_argument("--chunk-size-mb", type=int, default=DEFAULT_CHUNK_MB,
                     help=f"Chunk size in MB (default: {DEFAULT_CHUNK_MB}, i.e. ~1.9GB)")
    pd.add_argument("--keep-chunks", action="store_true", help="Don't delete chunk files after reassembly")
    add_proxy_args(pd)
    pd.set_defaults(func=cmd_download)

    pchk = sub.add_parser("check", help="Test proxy/connectivity to a URL before downloading")
    pchk.add_argument("url", help="URL to test (HEAD request only, no data downloaded)")
    add_proxy_args(pchk)
    pchk.set_defaults(func=cmd_check)

    ppr = sub.add_parser("probe", help="Empirically find the largest working chunk size for your network/proxy")
    ppr.add_argument("url", help="A real URL to test against (downloads real data during the probe)")
    ppr.add_argument("--min-mb", type=int, default=50, help="Smallest size to test, in MB (default: 50)")
    ppr.add_argument("--max-mb", type=int, default=3000, help="Largest size to test, in MB (default: 3000)")
    ppr.add_argument("--timeout", type=int, default=120, help="Per-attempt timeout in seconds (default: 120)")
    add_proxy_args(ppr)
    ppr.set_defaults(func=cmd_probe)

    pc = sub.add_parser("combine", help="Concatenate previously downloaded part files, in order, into one file")
    pc.add_argument("parts", nargs="+", help="Part files, in the order they should be joined")
    pc.add_argument("-o", "--output", required=True, help="Path for the combined output file")
    pc.set_defaults(func=cmd_combine)

    pe = sub.add_parser("extract", help="Extract a .tar/.tar.gz/.tgz archive")
    pe.add_argument("archive", help="Path to the archive file")
    pe.add_argument("-o", "--output-dir", default="./extracted", help="Where to extract (default: ./extracted)")
    pe.set_defaults(func=cmd_extract)

    pk = sub.add_parser("checksum", help="Print the SHA-256 checksum of a file")
    pk.add_argument("file", help="Path to the file")
    pk.set_defaults(func=cmd_checksum)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
