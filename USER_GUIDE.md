# User Guide: chunked_downloader.py

A cross-platform (Windows/macOS/Linux) tool for downloading large files
through a size-limited firewall/proxy, by fetching them in sub-2GB HTTP
Range requests and reassembling the pieces afterward.

For the fastest path to a working download, see **QUICKSTART.md**. This
guide covers every command and option in depth.

---

## Contents

1. [Installation](#1-installation)
2. [How it works, in brief](#2-how-it-works-in-brief)
3. [Command: `download`](#3-command-download)
4. [Command: `check`](#4-command-check)
5. [Command: `probe`](#5-command-probe)
6. [Command: `combine`](#6-command-combine)
7. [Command: `extract`](#7-command-extract)
8. [Command: `checksum`](#8-command-checksum)
9. [Proxy authentication](#9-proxy-authentication)
10. [TLS / SSL interception](#10-tls--ssl-interception)
11. [Resuming an interrupted download](#11-resuming-an-interrupted-download)
12. [Troubleshooting](#12-troubleshooting)
13. [Full option reference](#13-full-option-reference)

---

## 1. Installation

Requires **Python 3.8 or later** and the `requests` library.

| OS | Get Python | Install the dependency |
|---|---|---|
| Windows | https://python.org — check "Add python.exe to PATH" during setup | Open PowerShell or Command Prompt: `pip install requests` |
| macOS | Usually preinstalled (`python3 --version` to check); if not, https://python.org or `brew install python` | Terminal: `pip3 install requests` |
| Linux | Usually preinstalled; if not, use your package manager (`apt install python3 python3-pip`, `dnf install python3 python3-pip`, etc.) | Terminal: `pip3 install requests` |

If `pip`/`pip3` isn't recognized, use `python -m pip install requests`
(Windows) or `python3 -m pip install requests` (macOS/Linux).

Verify it worked:

```
python chunked_downloader.py --help
```

(On macOS/Linux you may need `python3` instead of `python`, depending on
how your system has Python installed.)

---

## 2. How it works, in brief

Amazon S3 (and most large-file hosts) support the HTTP `Range` header,
which lets a client request just part of a file — for example, "give me
bytes 0 through 1,999,999,999 of this object." This tool:

1. Sends a `HEAD` request to find the file's total size.
2. Splits that size into chunks no larger than your configured limit
   (default ~1.9GB, safely under a typical 2GB proxy cap).
3. Downloads each chunk with a `Range` request, retrying with backoff on
   any network error.
4. Once every chunk for a file is present and the right size, concatenates
   them, in order, into the final file — verified against the size the
   server originally reported.
5. Deletes the temporary chunk files (unless you pass `--keep-chunks`).

Because each chunk is checked against its expected size before being
"accepted," you can stop the tool at any point (Ctrl+C, network drop,
laptop sleep) and re-run the exact same command later — completed chunks
are detected and skipped.

---

## 3. Command: `download`

Downloads one or more URLs, chunked, and reassembles each into a single
file.

### Syntax

```
python chunked_downloader.py download [URLS...] [OPTIONS]
```

### Options

| Option | Meaning |
|---|---|
| `urls` (positional) | One or more URLs directly on the command line |
| `--urls-file FILE` | A text file with one URL per line (`#` starts a comment). Combine with positional URLs if you like — both are used. |
| `-o, --output-dir DIR` | Where downloaded/reassembled files go. Default: `./downloads` |
| `--chunk-size-mb N` | Chunk size in megabytes (MiB — see section 5's note on units). Default: `1900` (~1.9GB). Lower this if your proxy's limit is under 2GB, or run `probe` to find the exact right value. |
| `--keep-chunks` | Don't delete the temporary `.chunks` folder after a file is successfully reassembled. Useful if you want to inspect them or reuse them for another combine attempt. |
| *(proxy/TLS options)* | See [section 9](#9-proxy-authentication) and [section 10](#10-tls--ssl-interception) |

### Examples

Download using the included URL list, into `./downloads`:

```
python chunked_downloader.py download --urls-file urls.txt -o ./downloads
```

Download two specific URLs directly, with a smaller 900MB chunk size
(e.g. your proxy caps requests at 1GB):

```
python chunked_downloader.py download --chunk-size-mb 900 -o ./downloads \
  "https://example.com/file-a.tgz" \
  "https://example.com/file-b.tgz"
```

Windows PowerShell version of the same command (backtick is the
line-continuation character instead of `\`):

```
python chunked_downloader.py download --chunk-size-mb 900 -o .\downloads `
  "https://example.com/file-a.tgz" `
  "https://example.com/file-b.tgz"
```

Keep the chunk files around after downloading (e.g. for debugging):

```
python chunked_downloader.py download --urls-file urls.txt --keep-chunks
```

### What the output looks like

For each URL, you'll see the file size, whether the server advertises
range support, each chunk's progress, and — after reassembly — a
**format check** line:

```
== https://xdp-sovereign-artifacts.s3.us-east-1.amazonaws.com/.../part1.tgz
   size: 1.8GB  accept-ranges: True  etag: "a1b2c3..."
   chunk 0: bytes 0-1993410559 (1.9GB)  downloading...
   reassembling...
   done: downloads/xdp-sovereign-5.0.0-nifi-v1-part1.tgz  (1.8GB)
   format check: does NOT start with gzip magic bytes -> this is likely a
   raw fragment of a larger archive; you'll need to 'combine' it with
   adjacent part files before extracting.
```

That format check tells you whether to go straight to `extract`, or to
`combine` sibling parts first (see sections 6 and 7).

Not sure what `--chunk-size-mb` value to use? See [section 5](#5-command-probe)
for a tool that measures your proxy's real limit instead of guessing.

---

## 4. Command: `check`

A lightweight pre-flight test — sends a single `HEAD` request (no data
downloaded) to confirm a URL is reachable through your network/proxy
configuration before you commit to a large download.

### Syntax

```
python chunked_downloader.py check URL [OPTIONS]
```

### Examples

Basic check, using whatever proxy is set in your environment variables (or
none, if you're not behind a proxy):

```
python chunked_downloader.py check "https://xdp-sovereign-artifacts.s3.us-east-1.amazonaws.com/5.0.0-nifi-v1/xdp-sovereign-5.0.0-nifi-v1-part1.tgz"
```

Check with an explicit proxy and credentials:

```
python chunked_downloader.py check "https://example.com/file.tgz" \
  --proxy http://proxy.company.com:8080 --proxy-user alice --proxy-pass "s3cr3t"
```

### Reading the result

- `OK: status 200, size 1.8GB, Accept-Ranges: bytes` — you're good to run
  `download`.
- `FAILED: HTTP 407 Proxy Authentication Required` — your proxy needs
  credentials; see [section 9](#9-proxy-authentication).
- `FAILED: TLS/SSL error ...` — your proxy is likely intercepting HTTPS;
  see [section 10](#10-tls--ssl-interception).
- `FAILED: proxy error ...` — often means the proxy needs NTLM/Kerberos
  auth that this tool (or any plain `requests`-based tool) can't negotiate
  directly; see [section 9](#9-proxy-authentication).

---

## 5. Command: `probe`

**Not sure what chunk size your proxy actually allows?** Don't guess —
measure it. This command tests real `Range` requests of increasing size
against a URL you give it (discarding the received data, nothing is
written to disk) to find the largest request your network path actually
delivers in full, then recommends a `--chunk-size-mb` value with a safety
margin already applied.

This sidesteps the MB-vs-GB ambiguity entirely (see the callout box at the
end of this section) by finding your *actual* limit empirically instead of
assuming a definition.

### Syntax

```
python chunked_downloader.py probe URL [OPTIONS]
```

### Options

| Option | Meaning |
|---|---|
| `url` (positional) | A real, reachable URL to test against. Using one of your actual target files is fine and recommended — it exercises the exact path you'll use for the real download. |
| `--min-mb N` | Smallest size to test, in MB. Default: `50` |
| `--max-mb N` | Largest size to test, in MB. Default: `3000` |
| `--timeout N` | Per-attempt timeout in seconds. Default: `120` |
| *(proxy/TLS options)* | Same as `download`/`check` — see [section 9](#9-proxy-authentication) |

### Example

```
python chunked_downloader.py probe "https://xdp-sovereign-artifacts.s3.us-east-1.amazonaws.com/5.0.0-nifi-v1/xdp-sovereign-5.0.0-nifi-v1-part1.tgz"
```

### How it works

1. Doubles the test size (50MB, 100MB, 200MB, 400MB, ...) until a request
   fails or is truncated, or it reaches `--max-mb`.
2. If a failure was found, binary-searches between the last success and
   first failure to narrow in on the real boundary (to within 25MB).
3. Recommends a value 5% (or at least 25MB, whichever is larger) below the
   largest size that actually worked, rounded down to the nearest 50MB.

### Example output

```
Probing: https://.../xdp-sovereign-5.0.0-nifi-v1-part1.tgz
   file size: 1.8GB  accept-ranges: True
   testing sizes between 50.0MB and 1.8GB ...

   trying 50MB ... OK
   trying 100MB ... OK
   trying 200MB ... OK
   trying 400MB ... OK
   trying 800MB ... OK
   trying 1600MB ... FAILED / truncated
   trying 1200MB ... OK
   trying 1400MB ... FAILED / truncated
   ...

Largest size that downloaded successfully: 1.2GB

Recommended setting:  --chunk-size-mb 1100
(That's 1.1GB, 1,153,433,600 bytes -- 60MB safety margin under the largest size that worked.)
```

Use the recommended value with `download`:
```
python chunked_downloader.py download --urls-file urls.txt -o ./downloads --chunk-size-mb 1100
```

> **Why "2GB" isn't a precise number.** `--chunk-size-mb` multiplies by
> 1,048,576 bytes (1024×1024, the "MiB" your OS file manager calls "MB"),
> not the decimal 1,000,000-byte definition some vendors use for "GB" in
> their documentation. Concretely: `--chunk-size-mb 1900` (the tool's
> default) = 1,992,294,400 bytes. If your proxy enforces a strict
> **decimal** 2GB cap (2,000,000,000 bytes), that leaves only ~7MB of
> headroom — uncomfortably tight. If it enforces **2 GiB**
> (2,147,483,648 bytes), the same default has ~155MB of headroom. Since
> you usually can't tell which definition applies to your specific proxy,
> `probe` measures it directly instead of relying on either assumption.

### A note on bandwidth and time

Each test attempt downloads real data (it's just discarded rather than
saved), so a full probe run can transfer a meaningful amount of data —
typically a few hundred MB to a couple GB depending on where your limit
turns out to be — and take a few minutes. This is a one-time setup step,
not something you need to run before every download.

---

## 6. Command: `combine`

Concatenates already-downloaded files, **in the exact order you list
them**, into one output file. Use this when `download`'s format check
reported that files are raw fragments of one larger archive (a common
pattern when a vendor splits a big `.tar.gz` with the Unix `split` command
before uploading — unrelated to this tool's own chunking, which happens
automatically during `download` and is already undone by the time you see
the reassembled file).

### Syntax

```
python chunked_downloader.py combine FILE1 FILE2 [FILE3 ...] -o OUTPUT
```

### Options

| Option | Meaning |
|---|---|
| `parts` (positional) | Two or more files, listed in the order they must be joined |
| `-o, --output PATH` | Path for the combined file (required) |

### Example

```
python chunked_downloader.py combine \
    downloads/xdp-sovereign-5.0.0-nifi-v1-part1.tgz \
    downloads/xdp-sovereign-5.0.0-nifi-v1-part2.tgz \
    downloads/xdp-sovereign-5.0.0-nifi-v1-part3.tgz \
    downloads/xdp-sovereign-5.0.0-nifi-v1-part4.tgz \
    downloads/xdp-sovereign-5.0.0-nifi-v1-part5.tgz \
    downloads/xdp-sovereign-5.0.0-nifi-v1-part6.tgz \
    -o downloads/xdp-sovereign-5.0.0-nifi-v1-combined.tgz
```

Windows PowerShell version:

```
python chunked_downloader.py combine `
    downloads\xdp-sovereign-5.0.0-nifi-v1-part1.tgz `
    downloads\xdp-sovereign-5.0.0-nifi-v1-part2.tgz `
    downloads\xdp-sovereign-5.0.0-nifi-v1-part3.tgz `
    downloads\xdp-sovereign-5.0.0-nifi-v1-part4.tgz `
    downloads\xdp-sovereign-5.0.0-nifi-v1-part5.tgz `
    downloads\xdp-sovereign-5.0.0-nifi-v1-part6.tgz `
    -o downloads\xdp-sovereign-5.0.0-nifi-v1-combined.tgz
```

After combining, it prints the same gzip-magic-bytes format check. If it
still doesn't look like valid gzip, double-check the order of the parts —
`part1` really must come first, `part2` second, and so on.

---

## 7. Command: `extract`

Extracts a `.tar`, `.tar.gz`, or `.tgz` archive using Python's built-in
`tarfile` module — no separate `tar` installation needed, even on Windows.

### Syntax

```
python chunked_downloader.py extract ARCHIVE [OPTIONS]
```

### Options

| Option | Meaning |
|---|---|
| `archive` (positional) | Path to the archive file |
| `-o, --output-dir DIR` | Where to extract to. Default: `./extracted` |

### Examples

```
python chunked_downloader.py extract downloads/xdp-sovereign-5.0.0-nifi-v1-combined.tgz -o ./extracted
python chunked_downloader.py extract downloads/xdp-soverign-v2-release-5.0.0-nifi-v1.tgz -o ./extracted
```

---

## 8. Command: `checksum`

Prints the SHA-256 hash of a file, in case you want to compare it against
a checksum published elsewhere (e.g. a vendor's release notes).

### Syntax

```
python chunked_downloader.py checksum FILE
```

### Example

```
python chunked_downloader.py checksum downloads/xdp-soverign-v2-release-5.0.0-nifi-v1.tgz
```

Note: this tool does not automatically verify checksums for you (the
server doesn't provide a plain checksum for range-downloaded, multipart-
uploaded S3 objects, since S3's ETag for such objects isn't a simple MD5).
Use this command to manually compare against a known-good value if one is
published.

---

## 9. Proxy authentication

### Recommended: environment variables

This keeps credentials out of your shell history and out of your process
list (visible to other users via `ps`/Task Manager if passed as a
command-line flag).

**macOS/Linux:**
```
export HTTPS_PROXY="http://username:password@proxy.company.com:8080"
export HTTP_PROXY="$HTTPS_PROXY"
python chunked_downloader.py download --urls-file urls.txt -o ./downloads
```

**Windows PowerShell:**
```
$env:HTTPS_PROXY = "http://username:password@proxy.company.com:8080"
$env:HTTP_PROXY = $env:HTTPS_PROXY
python chunked_downloader.py download --urls-file urls.txt -o .\downloads
```

**Windows Command Prompt:**
```
set HTTPS_PROXY=http://username:password@proxy.company.com:8080
set HTTP_PROXY=%HTTPS_PROXY%
python chunked_downloader.py download --urls-file urls.txt -o .\downloads
```

No extra flags are needed — the tool picks these up automatically.

### Explicit flags (alternative to environment variables)

| Option | Meaning |
|---|---|
| `--proxy URL` | Proxy address, e.g. `http://proxy.company.com:8080` |
| `--proxy-user USER` | Username for Basic auth to the proxy (requires `--proxy`) |
| `--proxy-pass PASS` | Password for Basic auth to the proxy (requires `--proxy`) |
| `--proxy-auth {basic,ntlm}` | Auth scheme. `basic` (default) is what most corporate HTTP proxies use. |
| `--no-env-proxy` | Ignore `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` entirely, even if set |

Available on the `download`, `check`, and `probe` subcommands.

Example:
```
python chunked_downloader.py check "https://example.com/file.tgz" \
  --proxy http://proxy.company.com:8080 --proxy-user alice --proxy-pass "s3cr3t"
```

### NTLM / Kerberos proxies

Many corporate Windows networks use a proxy that requires NTLM or Kerberos
authentication rather than plain Basic auth. The Python `requests` library
(which this tool is built on) **cannot negotiate NTLM/Kerberos with a
proxy** on its own — only Basic auth via a username and password.

If `check` reports a proxy error and your organization uses NTLM/Kerberos
proxy authentication, the standard fix is to run a small local proxy
"shim" that handles that handshake for you and exposes a plain,
crendential-free proxy locally:

- **cntlm** (Windows/macOS/Linux) — authenticates to the real NTLM proxy
  once using your credentials (stored, optionally hashed, in its config
  file), then listens on `127.0.0.1:3128` (default) for your tools to use.
- **Px** (cross-platform, Python-based) — similar idea; on Windows it can
  often use your logged-in Windows session credentials automatically (SSO),
  without you typing a password into its config at all.

Once the shim is running, point this tool at it instead of the real proxy:

```
python chunked_downloader.py download --urls-file urls.txt -o ./downloads \
  --proxy http://127.0.0.1:3128
```

(Or set `HTTPS_PROXY=http://127.0.0.1:3128` as an environment variable —
same effect, no `--proxy` flag needed.)

---

## 10. TLS / SSL interception

Some corporate proxies decrypt and re-encrypt HTTPS traffic using their own
"root" certificate so they can inspect it — a deliberate, sanctioned setup
in many organizations, but it means your Python/`requests` installation
won't recognize that certificate as trusted by default, and you'll see an
SSL error.

| Option | Meaning |
|---|---|
| `--ca-bundle PATH` | Path to a `.pem` file containing your organization's proxy root certificate (or full chain). Ask your IT/Information Security team for this file. |
| `--insecure` | Disables certificate verification entirely. Only use this as a last resort — it removes protection against a genuinely malicious man-in-the-middle, not just your own IT-sanctioned one. |

Example, preferred approach:
```
python chunked_downloader.py download --urls-file urls.txt -o ./downloads \
  --ca-bundle "C:\certs\corp-proxy-root-ca.pem"
```

Example, last resort:
```
python chunked_downloader.py download --urls-file urls.txt -o ./downloads --insecure
```

---

## 11. Resuming an interrupted download

Nothing special to do — just re-run the exact same `download` command.

- Any chunk file that already exists **and** is the exact expected size is
  skipped.
- A chunk that's partially written (network dropped mid-chunk) resumes
  from the byte it left off at, using another `Range` request — it does
  not restart that chunk from zero.
- A file that was already fully reassembled and matches the server's
  reported size is skipped entirely, and the tool moves on to the next
  URL.

This also means it's safe to run the same command from a scheduled task or
script repeatedly (e.g. overnight) until everything completes.

---

## 12. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `ERROR: this script requires the 'requests' package` | Run `pip install requests` (or `pip3`, or `python -m pip install requests`) |
| `FAILED: HTTP 407 Proxy Authentication Required` | Add `--proxy-user`/`--proxy-pass`, or embed credentials in your `HTTP_PROXY`/`HTTPS_PROXY` environment variable |
| `FAILED: proxy error ...` | Often an NTLM/Kerberos proxy — see [section 9](#9-proxy-authentication) |
| `FAILED: TLS/SSL error ...` | Corporate HTTPS interception — see [section 10](#10-tls--ssl-interception) |
| Chunk size mismatch / repeated retries on one chunk | Your proxy may be silently truncating responses below your configured `--chunk-size-mb`; run `probe` (section 5) to find the real limit, then set `--chunk-size-mb` below it |
| Not sure what `--chunk-size-mb` to use in the first place | Run `probe` (section 5) instead of guessing — see also the MB-vs-GB note in that section |
| Reassembled file's format check says "does NOT start with gzip magic bytes" | Expected for split archive fragments — use `combine` first (section 6) |
| `combine` output still doesn't look like valid gzip | Double check the order of files passed to `combine` — it must match the original split order (`part1`, `part2`, ...) |
| Extraction fails with "not a gzip file" or similar | You likely tried to extract a raw fragment directly instead of the `combine`d file — see section 6 |
| Very slow chunk downloads | Try a smaller `--chunk-size-mb` so failures cost you less retried data, and confirm with `check` that Range requests are actually being honored (`Accept-Ranges: bytes` in the output) |

---

## 13. Full option reference

```
python chunked_downloader.py download [urls ...]
    --urls-file FILE
    -o, --output-dir DIR         (default: ./downloads)
    --chunk-size-mb N            (default: 1900)
    --keep-chunks
    --proxy URL
    --proxy-user USER
    --proxy-pass PASS
    --proxy-auth {basic,ntlm}    (default: basic)
    --no-env-proxy
    --insecure
    --ca-bundle PATH

python chunked_downloader.py check URL
    --proxy URL
    --proxy-user USER
    --proxy-pass PASS
    --proxy-auth {basic,ntlm}
    --no-env-proxy
    --insecure
    --ca-bundle PATH

python chunked_downloader.py probe URL
    --min-mb N                   (default: 50)
    --max-mb N                   (default: 3000)
    --timeout N                  (default: 120)
    --proxy URL
    --proxy-user USER
    --proxy-pass PASS
    --proxy-auth {basic,ntlm}
    --no-env-proxy
    --insecure
    --ca-bundle PATH

python chunked_downloader.py combine FILE1 FILE2 [FILE3 ...]
    -o, --output PATH            (required)

python chunked_downloader.py extract ARCHIVE
    -o, --output-dir DIR         (default: ./extracted)

python chunked_downloader.py checksum FILE
```

Every subcommand also supports `-h`/`--help` for the same information at
the terminal, e.g. `python chunked_downloader.py download --help`.
