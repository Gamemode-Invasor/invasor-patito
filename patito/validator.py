"""lsfg-vk's own config validator: `lsfg-vk-cli validate -c <file>` (lsfg-vk 2.0+).

Used as a second safety net before Patito replaces conf.toml: lsfg-vk judges its own
format (it rejects, e.g., unknown keys and out-of-range values), so a newer lsfg-vk's
rules are honoured even when Patito doesn't know them. Optional: without lsfg-vk-cli
(older or differently packaged installs) Patito relies on its own checks.

Pure stdlib, no Invasor imports.
"""
import shutil
import subprocess
from pathlib import Path

TIMEOUT = 5
CLI = "lsfg-vk-cli"


class Rejected(Exception):
    """lsfg-vk says the file isn't valid; the message is lsfg-vk's own."""


def find_cli(library=None):
    """lsfg-vk-cli next to the layer library in use (<prefix>/bin), else on PATH, else None."""
    if library:
        candidate = Path(library).parent.parent / "bin" / CLI
        if candidate.is_file():
            return str(candidate)
    return shutil.which(CLI)


def validate(path, cli):
    """None if lsfg-vk accepts the file. Raises Rejected with lsfg-vk's message if not,
    or OSError/TimeoutError if the validator can't be run (treat as unavailable)."""
    try:
        run = subprocess.run([cli, "validate", "-c", str(path)], capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"{CLI} didn't answer in {TIMEOUT}s") from None
    if run.returncode == 0:
        return None
    lines = [ln.strip() for ln in (run.stdout + "\n" + run.stderr).splitlines() if ln.strip()]
    # The first line only echoes the file being validated; the reason follows it.
    reasons = [ln for ln in lines if not ln.startswith("Validating configuration file")]
    raise Rejected(reasons[0] if reasons else f"{CLI} exited with {run.returncode}")
