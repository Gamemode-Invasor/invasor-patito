"""lsfg-vk updates: find the latest stable release on builds.lsfg-vk.dev and install it
into ~/.local, the way lsfg-vk's own docs do (`tar -xvf lsfg-vk-X.tar.xz -C ~/.local`).

Pure stdlib, no Invasor imports: tested on its own (tests/test_updates.py).

- What's installed is read from lsfg-vk itself: its Vulkan layer library carries its
  exact version as a string ("2.0.0", "2.0.0.r1.g0e7a389"…), so it's right however
  lsfg-vk was installed (by hand, another tool or Ducky). No marker file is used.
- The release index is builds.lsfg-vk.dev: its "Latest release (X)" row links the
  stable tarball lsfg-vk-X.tar.xz (git/RC builds are never offered).
- Installing checks every entry first (relative paths under bin/, lib/ or share/,
  regular files and folders only), unpacks to a staging folder inside the prefix,
  moves each file into place atomically, then checks the installed version.
"""
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

INDEX = "https://builds.lsfg-vk.dev/"
HOST = "builds.lsfg-vk.dev"
USER_AGENT = "invasor-ducky"
TIMEOUT = 20
MAX_DOWNLOAD = 64 << 20
PREFIX = Path.home() / ".local"
ALLOWED_TOP = ("bin", "lib", "share")
LAYER_JSON = "VkLayer_LSFGVK_frame_generation.json"
LAYER = f"share/vulkan/implicit_layer.d/{LAYER_JSON}"
# Where Vulkan looks for implicit layers: the user's first, then the system's.
LAYER_DIRS = (
    PREFIX / "share/vulkan/implicit_layer.d",
    Path("/usr/local/share/vulkan/implicit_layer.d"),
    Path("/usr/share/vulkan/implicit_layer.d"),
    Path("/etc/vulkan/implicit_layer.d"),
)

STABLE_NAME = re.compile(r"^lsfg-vk-(\d+)\.(\d+)\.(\d+)\.tar\.xz$")
VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?(?:\.r(\d+)\.g[0-9a-f]+)?")
# The version string inside the layer library: a whole NUL-separated string.
EMBEDDED = re.compile(rb"(?<![\w.-])(\d{1,3}\.\d{1,3}\.\d{1,3}(?:-rc\d+)?(?:\.r\d+\.g[0-9a-f]{7,12})?)\x00")
LATEST_RELEASE = re.compile(r'<a href="([^"]+)">[^<]*Latest release \(', re.I)


# The lsfg-vk series this Ducky was written and tested against. Same major, newer minor:
# usable (new options just don't show here). Another major: never installed by Ducky.
TESTED = (2, 0)
SUPPORTED_MAJOR = 2


def compatibility(version):
    """"tested", "newer_minor", "older" or "unsupported" (another major version); None if unknown."""
    v = parse_version(version)
    if v is None:
        return None
    if v[0] != SUPPORTED_MAJOR:
        return "unsupported"
    if (v[0], v[1]) == TESTED:
        return "tested"
    return "newer_minor" if (v[0], v[1]) > TESTED else "older"


class UpdateError(Exception):
    pass


def parse_version(text):
    """Sortable version, or None: 2.0.0-rc1 < 2.0.0-rc1.r5 < 2.0.0 < 2.0.0.r1 < 2.0.1."""
    m = VERSION.search(text or "")
    if not m:
        return None
    major, minor, patch, rc, commits = m.groups()
    stage = (0, int(rc)) if rc is not None else (1, 0)  # a release candidate sorts before its release
    return (int(major), int(minor), int(patch), stage, int(commits or 0))


def label(name):
    """"lsfg-vk-2.0.0.tar.xz" -> "2.0.0"."""
    return re.sub(r"^lsfg-vk-|\.tar\.xz$", "", name) if name else None


def compare(installed, latest):
    """"newer_available", "up_to_date" or "ahead" (installed is past the latest stable,
    e.g. a git build). An unknown installed version can always take the stable."""
    new = parse_version(latest)
    if new is None:
        raise UpdateError(f"can't read the latest version {latest!r}")
    have = parse_version(installed)
    if have is None or have < new:
        return "newer_available"
    return "up_to_date" if have == new else "ahead"


def latest_stable(html):
    """The stable tarball's name from the index page ("Latest release" row)."""
    m = LATEST_RELEASE.search(html or "")
    if not m:
        raise UpdateError("the release index has no 'Latest release' entry")
    name = urllib.parse.unquote(m.group(1)).rsplit("/", 1)[-1]
    if not STABLE_NAME.match(name):
        raise UpdateError(f"unexpected release file {name!r}")
    return name


# ---------- what's installed ----------

def library_version(path):
    """The version lsfg-vk compiled into its layer library, or None if not found."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    for m in EMBEDDED.finditer(data):
        return m.group(1).decode()
    return None


def find_layer(dirs=LAYER_DIRS):
    """(layer json, library path) of the lsfg-vk layer Vulkan finds first, or None."""
    for d in dirs:
        manifest = Path(d) / LAYER_JSON
        try:
            lib = json.loads(manifest.read_text())["layer"]["library_path"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        lib_path = Path(lib) if os.path.isabs(lib) else (manifest.parent / lib)
        return manifest, Path(os.path.normpath(lib_path))
    return None


def installed(dirs=LAYER_DIRS, prefix=PREFIX):
    """{version, path, local}: lsfg-vk's version as its library reports it (None if it
    can't be read), the library's path, and whether it lives under ~/.local (the only
    place Ducky updates). None if lsfg-vk isn't installed."""
    found = find_layer(dirs)
    if not found:
        return None
    _, lib = found
    try:
        local = Path(lib).resolve().is_relative_to(Path(prefix).resolve())
    except OSError:
        local = False
    return {"version": library_version(lib), "path": str(lib), "local": local}


# ---------- downloading and installing ----------

def _get(url, limit, opener):
    parts = urllib.parse.urlparse(url)
    if parts.scheme != "https" or parts.hostname != HOST:
        raise UpdateError(f"refusing to download from {parts.hostname or url!r}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener(req, timeout=TIMEOUT) as res:
            data = res.read(limit + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError(f"can't reach {HOST} ({getattr(e, 'reason', e)})") from None
    if len(data) > limit:
        raise UpdateError("download too large")
    return data


def fetch_latest(opener=urllib.request.urlopen):
    return latest_stable(_get(INDEX, 1 << 20, opener).decode("utf-8", "replace"))


def download(name, opener=urllib.request.urlopen):
    if not STABLE_NAME.match(name):
        raise UpdateError(f"not a stable release: {name!r}")
    return _get(INDEX + urllib.parse.quote(name), MAX_DOWNLOAD, opener)


def _members(tar):
    """Every entry, checked; (relative path, member) of the regular files to install."""
    files = []
    for m in tar.getmembers():
        path = PurePosixPath(m.name)
        parts = [p for p in path.parts if p not in (".", "")]
        if path.is_absolute() or ".." in parts:
            raise UpdateError(f"unsafe path in the archive: {m.name!r}")
        if not parts:
            continue
        if parts[0] not in ALLOWED_TOP:
            raise UpdateError(f"unexpected file in the archive: {m.name!r}")
        if m.isdir():
            continue
        if not m.isfile():
            raise UpdateError(f"the archive contains a link or special file: {m.name!r}")
        files.append(("/".join(parts), m))
    if not any(rel == LAYER for rel, _ in files):
        raise UpdateError("this archive doesn't contain lsfg-vk's Vulkan layer")
    return files


def install(data, expected, prefix=PREFIX):
    """Install a release tarball into `prefix`. Nothing is changed unless the whole
    archive is valid; afterwards the installed layer must report `expected`.
    Returns the list of installed files (relative)."""
    try:
        tar = tarfile.open(fileobj=io.BytesIO(data), mode="r:xz")
    except (tarfile.TarError, EOFError, OSError) as e:
        raise UpdateError(f"not a valid .tar.xz: {e}") from None
    with tar:
        files = _members(tar)
        prefix.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".lsfg-vk-update-", dir=prefix))
        try:
            for rel, m in files:
                target = staging / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(m) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                os.chmod(target, 0o755 if m.mode & 0o111 else 0o644)
            for rel, _ in files:  # everything is unpacked: now put it in place
                dest = prefix / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging / rel, dest)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    found = installed([prefix / "share/vulkan/implicit_layer.d"], prefix)
    if not found or found["version"] != expected:
        raise UpdateError(f"installed, but the layer reports {found and found['version']!r} instead of {expected!r}")
    return [rel for rel, _ in files]
