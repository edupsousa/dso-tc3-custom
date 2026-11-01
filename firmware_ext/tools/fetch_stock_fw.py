#!/usr/bin/env python3
"""Fetch and extract the official FNIRSI DSO-TC3 stock firmware.

Downloads the official firmware archive directly from FNIRSI's CDN, verifies
the SHA-256 integrity checksum, and extracts the stock DSO-TC3_V0.3.s19 file.

The stock firmware is proprietary to FNIRSI (Shenzhen FNIRSI Technology Co., Ltd.)
and is downloaded directly from FNIRSI's public distribution servers.
"""

import argparse
import hashlib
import io
import os
import sys
import urllib.request
import zipfile

DEFAULT_URL = (
    "https://cdn.shopify.com/s/files/1/0694/8310/2426/files/"
    "DSO_TC3_Firmware_V0.3.zip?v=1756205795"
)
ZIP_SHA256 = "0fbea840eee8d80da20fcd5d6eb5cb3cc26315fb5bf48fde55ffd2783bf16e24"
S19_SHA256 = "9a97d5bb1a5864af6484b92f115ae827e16e3972f5ec8828362c358ba2430b61"
S19_FILENAME = "DSO-TC3_V0.3.s19"
S19_ZIP_PATH = "V0.3/DSO-TC3_V0.3.s19"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def download_zip(url: str) -> bytes:
    print(f"Downloading official firmware archive from:\n  {url}")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; dso-tc3-fetch/1.0)"},
    )
    with urllib.request.urlopen(req) as resp:
        total = resp.getheader("Content-Length")
        total_len = int(total) if total and total.isdigit() else 0
        chunks = []
        downloaded = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            chunks.append(chunk)
            downloaded += len(chunk)
            if total_len:
                pct = downloaded * 100 // total_len
                print(f"\rProgress: {downloaded}/{total_len} bytes ({pct}%)", end="", flush=True)
            else:
                print(f"\rProgress: {downloaded} bytes", end="", flush=True)
        print()
    return b"".join(chunks)


def extract_s19_from_zip(zip_data: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(zip_data)) as z:
        candidates = [n for n in z.namelist() if n.endswith(S19_FILENAME)]
        if not candidates:
            raise RuntimeError(f"Could not find {S19_FILENAME} inside zip archive.")
        target = candidates[0]
        return z.read(target)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_URL, help="URL to firmware zip archive")
    ap.add_argument("--from-zip", help="Path to already downloaded local zip file")
    ap.add_argument("-o", "--out", help=f"Destination path (default: ./{S19_FILENAME})")
    ap.add_argument("--check-only", help="Verify SHA256 of an existing .s19 or .zip file")
    args = ap.parse_args(argv)

    if args.check_only:
        target = args.check_only
        if not os.path.isfile(target):
            print(f"Error: file not found: {target}", file=sys.stderr)
            return 1
        digest = sha256_file(target)
        if digest == S19_SHA256:
            print(f"OK: {target} is a valid stock DSO-TC3_V0.3.s19 (SHA-256: {digest})")
            return 0
        elif digest == ZIP_SHA256:
            print(f"OK: {target} is a valid official firmware ZIP (SHA-256: {digest})")
            return 0
        else:
            print(f"MISMATCH: {target} has unexpected SHA-256: {digest}", file=sys.stderr)
            return 2

    # Determine default destination
    dest = args.out
    if not dest:
        # If run from repo root, place in repo root
        dest = S19_FILENAME

    # If destination already exists and is valid, inform user
    if os.path.isfile(dest):
        if sha256_file(dest) == S19_SHA256:
            print(f"Stock firmware already present and verified at: {dest}")
            return 0

    if args.from_zip:
        print(f"Reading local zip file: {args.from_zip}")
        zip_bytes = open(args.from_zip, "rb").read()
    else:
        zip_bytes = download_zip(args.url)

    # Check zip hash if downloaded from default url
    zip_digest = sha256_bytes(zip_bytes)
    if not args.from_zip:
        if zip_digest != ZIP_SHA256:
            print(
                f"WARNING: Zip archive SHA-256 differs from known V0.3 release:\n"
                f"  got:      {zip_digest}\n"
                f"  expected: {ZIP_SHA256}",
                file=sys.stderr,
            )

    print(f"Extracting {S19_FILENAME}...")
    s19_data = extract_s19_from_zip(zip_bytes)

    # Verify extracted .s19 SHA256
    s19_digest = sha256_bytes(s19_data)
    if s19_digest != S19_SHA256:
        print(
            f"ERROR: Extracted {S19_FILENAME} SHA-256 mismatch!\n"
            f"  got:      {s19_digest}\n"
            f"  expected: {S19_SHA256}",
            file=sys.stderr,
        )
        return 2

    dest_dir = os.path.dirname(dest)
    if dest_dir:
        os.makedirs(dest_dir, exist_ok=True)

    with open(dest, "wb") as f:
        f.write(s19_data)

    print(f"SUCCESS: Stock firmware extracted to: {dest}")
    print(f"  Size:    {len(s19_data):,} bytes")
    print(f"  SHA-256: {s19_digest}")
    print("\nNote: DSO-TC3_V0.3.s19 is proprietary firmware of FNIRSI (Shenzhen FNIRSI Technology Co., Ltd.).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
