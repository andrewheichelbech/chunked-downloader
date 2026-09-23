# Quickstart

Get the included example binaries downloaded and extracted in 5 steps. For
every option explained in depth, see **USER_GUIDE.md**.

## 1. Install Python and the one dependency

- **Windows**: install Python from https://python.org (check "Add python.exe
  to PATH" during setup), then open PowerShell.
- **macOS**: Python 3 is usually preinstalled; open Terminal.
- **Linux**: Python 3 is usually preinstalled; open your terminal.

Then, on any OS:

```
pip install requests
```

(If that fails, try `pip3 install requests` or `python -m pip install requests`.)

## 2. Unzip and open a terminal in the folder

Unzip this package, then `cd` into the `chunked-downloader` folder it creates.

## 3. (If you're behind a proxy) confirm it works first

```
python chunked_downloader.py check "https://xdp-sovereign-artifacts.s3.us-east-1.amazonaws.com/5.0.0-nifi-v1/xdp-sovereign-5.0.0-nifi-v1-part1.tgz"
```

If this fails, see the "Proxy authentication" section of **USER_GUIDE.md**
before continuing. If it prints `OK: status 200 ...`, move on.

### Not sure the default chunk size is safe for your proxy?

The default (`1900`, meaning ~1.9GB) is usually fine, but "2GB" limits are
sometimes measured in decimal gigabytes (2,000,000,000 bytes) rather than
the 1,048,576-byte "MB" this tool uses — which can leave as little as ~7MB
of headroom. If you'd rather not guess, measure your proxy's actual limit:

```
python chunked_downloader.py probe "https://xdp-sovereign-artifacts.s3.us-east-1.amazonaws.com/5.0.0-nifi-v1/xdp-sovereign-5.0.0-nifi-v1-part1.tgz"
```

It downloads test-sized chunks (discarding them) and prints a recommended
`--chunk-size-mb` value with a safety margin built in — pass that value to
`download` in the next step. See **USER_GUIDE.md** section 5 for details.

## 4. Download everything

```
python chunked_downloader.py download --urls-file urls.txt -o ./downloads
```

This fetches all 7 files in ~1.9GB pieces (safely under a 2GB proxy limit)
and reassembles each one automatically. If it's interrupted, just run the
exact same command again — it resumes instead of starting over.

Watch the output for each file's **format check** line at the end — it
tells you what to do next:

- `looks like a standalone valid .tar.gz` → skip to step 5 for that file.
- `likely a raw fragment of a larger archive` → that file needs to be
  combined with its sibling `part` files first — see step 4b.

## 4b. Only if needed: combine split archive parts

If `part1` through `part6` were reported as raw fragments (not standalone
gzip files), join them into one valid archive, in order:

```
python chunked_downloader.py combine ^
    downloads\xdp-sovereign-5.0.0-nifi-v1-part1.tgz ^
    downloads\xdp-sovereign-5.0.0-nifi-v1-part2.tgz ^
    downloads\xdp-sovereign-5.0.0-nifi-v1-part3.tgz ^
    downloads\xdp-sovereign-5.0.0-nifi-v1-part4.tgz ^
    downloads\xdp-sovereign-5.0.0-nifi-v1-part5.tgz ^
    downloads\xdp-sovereign-5.0.0-nifi-v1-part6.tgz ^
    -o downloads\xdp-sovereign-5.0.0-nifi-v1-combined.tgz
```

(On macOS/Linux, replace the `^` line-continuation with `\` and the
backslashes in paths with forward slashes — see USER_GUIDE.md for
OS-specific versions of every command.)

## 5. Extract

```
python chunked_downloader.py extract downloads/xdp-sovereign-5.0.0-nifi-v1-combined.tgz -o ./extracted
python chunked_downloader.py extract downloads/xdp-soverign-v2-release-5.0.0-nifi-v1.tgz -o ./extracted
```

Done — your files are in `./extracted`.

---
Need more detail on any option, error message, or how proxies/NTLM/TLS
interception are handled? See **USER_GUIDE.md**.
