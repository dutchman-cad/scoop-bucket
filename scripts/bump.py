#!/usr/bin/env python3
"""Resolve public releases and verify Windows archives before updating manifests.

--check downloads and verifies exactly as a normal run, but writes nothing.
Exit status: 0 for success/up to date, 1 for pending changes with --check,
2 for resolution, download, checksum, or archive errors.
"""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com/repos/dutchman-cad"
PRODUCTS = (
    ("dutchman", "dutchman", "dutchman-{version}-x86_64-pc-windows-msvc",
     ("dutchman.exe", "dutchman-mcp.exe", "dutchman-gui.exe")),
    ("dutchman-pilot", "pilot", "pilot-{version}-x86_64-windows",
     ("dutchman-pilot.exe", "dutchman-pilot-eval.exe")),
)


def request(url):
    headers = {"User-Agent": "dutchman-cad-scoop-bucket"}
    if url.startswith("https://api.github.com/"):
        headers["Accept"] = "application/vnd.github+json"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
        if os.environ.get("GITHUB_TOKEN"):
            headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120)


def api(path):
    with request(f"{API}/{path}") as response:
        return json.load(response)


def usable(release):
    return not release.get("draft", False) and release["tag_name"] != "nightly"


def resolve_release(repo):
    # Once Pilot has a stable release it follows /latest, just like Convert.
    try:
        release = api(f"{repo}/releases/latest")
    except urllib.error.HTTPError as error:
        if repo != "pilot" or error.code != 404:
            raise
        error.close()
    else:
        if not usable(release) or release.get("prerelease", False):
            raise ValueError(f"{repo}: /latest returned an ineligible release")
        return release
    page = 1
    while True:
        releases = api(f"{repo}/releases?per_page=100&page={page}")
        if not releases:
            raise ValueError(f"{repo}: no non-nightly release found")
        for release in releases:
            if usable(release):
                return release
        page += 1


def checksum(text, filename):
    """Accept a bare digest, sha256sum format, or BSD SHA256 format."""
    for line in text.splitlines():
        line = line.strip()
        if re.fullmatch(r"[0-9a-fA-F]{64}", line):
            return line.lower()
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line)
        if match and match[2].removeprefix("./") == filename:
            return match[1].lower()
        match = re.fullmatch(r"SHA256 \((.+)\) = ([0-9a-fA-F]{64})", line)
        if match and match[1] == filename:
            return match[2].lower()
    raise ValueError(f"No SHA256 for {filename} in checksum asset")


def inspect_archive(file, folder, required):
    with zipfile.ZipFile(file) as archive:
        names = {entry.filename for entry in archive.infolist() if not entry.is_dir()}
        if not names:
            raise ValueError("Empty release archive")
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError(f"Unsafe archive path: {name}")
            if len(path.parts) < 2 or path.parts[0] != folder:
                raise ValueError(f"Expected a single top folder {folder}; found {name}")
        for executable in required:
            if f"{folder}/{executable}" not in names:
                raise ValueError(f"Missing {folder}/{executable}")
        print(f"  archive: {folder}/ ({len(names)} files); {', '.join(required)}")
    return folder


def update_product(name, repo, pattern, required):
    release = resolve_release(repo)
    tag = release["tag_name"]
    version = tag.removeprefix("v")
    if not re.fullmatch(r"[0-9][0-9A-Za-z.+-]*", version):
        raise ValueError(f"Unsupported release tag: {tag}")
    folder = pattern.format(version=version)
    filename = folder + ".zip"
    assets = {asset["name"]: asset for asset in release["assets"]}
    if filename not in assets:
        raise ValueError(f"{repo} {tag}: missing asset {filename}")
    url = assets[filename]["browser_download_url"]
    print(f"{name}: release {tag}; downloading {filename}")
    digest = hashlib.sha256()
    with tempfile.TemporaryFile() as archive:
        with request(url) as response:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                archive.write(chunk)
        actual_hash = digest.hexdigest()
        if filename + ".sha256" in assets:
            with request(assets[filename + ".sha256"]["browser_download_url"]) as response:
                expected = checksum(response.read().decode("utf-8-sig"), filename)
            if expected != actual_hash:
                raise ValueError(f"{filename}: SHA256 mismatch: expected {expected}, got {actual_hash}")
            print(f"  SHA256 verified against .sha256: {actual_hash}")
        else:
            print(f"  SHA256 computed (no .sha256 asset): {actual_hash}")
        archive.seek(0)
        extract_dir = inspect_archive(archive, folder, required)
    path = ROOT / "bucket" / f"{name}.json"
    original = json.loads(path.read_text(encoding="utf-8"))
    updated = copy.deepcopy(original)
    updated["version"] = version
    updated["architecture"]["64bit"].update(url=url, hash=actual_hash)
    updated["extract_dir"] = extract_dir
    # Capture the real tag (including any v prefix) in the update template.
    updated["autoupdate"]["architecture"]["64bit"]["url"] = url.replace(
        f"/download/{tag}/", f"/download/{tag.replace(version, '$version', 1)}/", 1
    ).replace(filename, pattern.format(version="$version") + ".zip")
    changes = []
    for field in ("version", "extract_dir", "architecture", "autoupdate"):
        if original.get(field) != updated[field]:
            changes.append(f"  {field}: {json.dumps(original.get(field))} -> {json.dumps(updated[field])}")
    if changes:
        print("\n".join(changes))
    else:
        print(f"{name}: up to date ({version})")
    return path, updated, bool(changes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report without writing; exit 1 if changes are pending")
    args = parser.parse_args()
    try:
        # Validate both products before changing either manifest.
        updates = [update_product(*product) for product in PRODUCTS]
        changed = False
        for path, manifest, needs_update in updates:
            if needs_update:
                changed = True
                if not args.check:
                    path.write_text(json.dumps(manifest, indent=4) + "\n", encoding="utf-8")
                    print(f"Wrote {path.relative_to(ROOT)}")
        return 1 if args.check and changed else 0
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(f"bump: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
