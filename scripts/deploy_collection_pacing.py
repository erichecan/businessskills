#!/usr/bin/env python3
"""Install or verify local collection pacing without starting scheduled jobs."""
import argparse
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from collection_pacing import directory, settings

ROOT = Path(__file__).resolve().parents[1]
MC = Path.home() / ".mediacrawler"


def git_check(patch, reverse=False):
    args = ["git", "apply", "--check"]
    if reverse:
        args.append("--reverse")
    result = subprocess.run([*args, str(patch)], cwd=MC, capture_output=True, text=True)
    return result.returncode == 0


def ensure_link(path, target, check):
    if path.is_symlink() and path.resolve() == target.resolve():
        print(f"OK shared module: {path}")
        return
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"Unexpected existing module; refusing to replace: {path}")
    if check:
        raise RuntimeError(f"Missing shared module: {path}")
    path.symlink_to(target)
    print(f"Installed shared module: {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify deployment without changes")
    args = parser.parse_args()
    if not MC.is_dir():
        raise RuntimeError(f"MediaCrawler not installed: {MC}")
    for name in ("20261005-preserve-login.patch", "20261005-collection-pacing.patch"):
        patch = ROOT / "scripts/xhs-probe/patches" / name
        if git_check(patch, reverse=True):
            print(f"OK patch: {name}")
        elif not args.check and git_check(patch):
            backup = MC / "local-patch-backups" / datetime.now().strftime("%Y%m%d-%H%M%S")
            for line in patch.read_text().splitlines():
                if line.startswith("+++ b/"):
                    rel = line[len("+++ b/"):]
                    destination = backup / rel
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(MC / rel, destination)
            subprocess.run(["git", "apply", str(patch)], cwd=MC, check=True)
            print(f"Installed patch: {name}; backup: {backup}")
        else:
            raise RuntimeError(f"Patch missing or incompatible: {name}")
    module = ROOT / "scripts/collection_pacing.py"
    ensure_link(MC / "tools/collection_pacing.py", module, args.check)
    xima = ROOT.parent / "ximalaya"
    ensure_link(xima / "src/collection_pacing.py",
                xima / "src/../../businessskills/scripts/collection_pacing.py", args.check)
    config = directory() / "pacing.json"
    if not config.exists():
        if args.check:
            raise RuntimeError(f"Missing pacing settings: {config}")
        config.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "config/collection-pacing.json", config)
    print(f"OK settings: {config}; jitter_ratio={settings()['jitter_ratio']}")
    print("Local deployment ready. Scheduled jobs load changes on their next run.")


if __name__ == "__main__":
    main()
