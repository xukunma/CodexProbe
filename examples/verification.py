"""Grade the checkout fixture with a fixed suite under a Codex permissions profile."""
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory


def profile(directory: Path, access: str) -> dict:
    """Grant the interpreter and fixture only the access required for this run."""
    prefix = "permissions.checkout"
    return {
        f"{prefix}.filesystem": {":minimal": "read", str(Path(sys.base_prefix).resolve()): "read",
                                 str(directory.resolve()): access},
        f"{prefix}.network.enabled": False,
    }


def regular_bytes(path: Path) -> bytes | None:
    """Return the contents of a regular file in the exercise, or None if unavailable."""
    if path.is_symlink():
        return None

    def open_source(name, flags):
        return os.open(name, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))

    try:
        with open(path, "rb", opener=open_source) as stream:
            if stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return stream.read()
    except OSError:
        pass
    return None


def overrides(settings: dict) -> list[str]:
    args = []
    for key, value in settings.items():
        if isinstance(value, dict):
            # Pass paths as literal TOML keys, including paths containing a period.
            literal = "{" + ", ".join(json.dumps(k) + "=" + json.dumps(v) for k, v in value.items()) + "}"
        else:
            literal = json.dumps(value)
        args.extend(["-c", key + "=" + literal])
    return args


def verify(source: bytes | None, suite: bytes) -> dict:
    """Run the original suite in a fresh, read-only, offline fixture."""
    result = {"exit_code": None, "ran": 0, "passed": 0, "output": ""}
    if source is None:
        result["output"] = "Missing or non-regular checkout.py"
        return result
    cli = shutil.which("codex")
    if not cli:
        result["output"] = "Codex CLI unavailable for isolated verification"
        return result

    with TemporaryDirectory(prefix="checkout-verify-") as name:
        clean = Path(name).resolve()
        (clean / "checkout.py").write_bytes(source)
        (clean / "test_checkout.py").write_bytes(suite)
        python = str(Path(sys._base_executable).resolve())
        cmd = [cli, "sandbox", "-C", str(clean), "-P", "checkout", *overrides(profile(clean, "read")),
               "--", python, "-IBS", "-m", "unittest", "discover", "-s", ".", "-v"]
        env = {"PATH": os.defpath, "LANG": "C"}
        if sys.platform == "win32":
            env["SystemRoot"] = os.environ["SystemRoot"]
        try:
            run = subprocess.run(cmd, cwd=clean, env=env, stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True, errors="replace", timeout=25)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["output"] = f"Isolated verifier: {type(exc).__name__}"
            return result

    output = run.stdout + run.stderr
    total = re.search(r"^Ran (\d+) tests? in ", output, re.MULTILINE)
    result.update(exit_code=run.returncode, output=output,
                  ran=int(total[1]) if total else 0,
                  passed=len(re.findall(r"^test_.* \.\.\. ok$", output, re.MULTILINE)))
    return result
