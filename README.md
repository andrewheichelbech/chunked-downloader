# Chunked Downloader

Downloads large files through a size-limited firewall/proxy by fetching
them in sub-2GB HTTP Range requests, then reassembles the pieces into the
original file. Pure Python, works the same way on Windows, macOS, and
Linux.

- **New to this tool?** Start with **QUICKSTART.md** — five steps to a
  finished download and extraction of the included example URLs.
- **Need details on a specific option, proxy/NTLM/TLS setup, or
  troubleshooting?** See **USER_GUIDE.md** — every command and flag
  explained with examples.

## Files in this package

| File | Purpose |
|---|---|
| `chunked_downloader.py` | The tool itself |
| `urls.txt` | Pre-filled with example URLs (the 7 xDP 5.0.0 NiFi v1 files) — edit or replace with your own |
| `QUICKSTART.md` | Fastest path to a working download |
| `USER_GUIDE.md` | Full reference: every option, explained, with examples |
