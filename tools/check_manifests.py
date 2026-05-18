#!/usr/bin/env python3
"""Parse every manifest under kubernetes-labs/ as YAML (multi-doc aware) and
sanity-check that each document looks like a Kubernetes object.

This is a syntax + shape check, not full schema validation - it does not need a
cluster or network. Run via `make validate` or in CI.
"""
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("pyyaml is required: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent / "kubernetes-labs"


def main() -> int:
    files = sorted(list(ROOT.rglob("*.yaml")) + list(ROOT.rglob("*.yml")))
    if not files:
        print("no manifests found", file=sys.stderr)
        return 1
    docs = 0
    failed = 0
    for f in files:
        rel = f.relative_to(ROOT.parent)
        try:
            loaded = list(yaml.safe_load_all(f.read_text(encoding="utf-8")))
        except yaml.YAMLError as e:
            failed += 1
            print(f"FAIL  {rel}: {str(e).splitlines()[0]}")
            continue
        for d in loaded:
            if not d:  # None or an empty {} (comment-only / stub files)
                continue
            docs += 1
            if not isinstance(d, dict) or "kind" not in d or "apiVersion" not in d:
                failed += 1
                print(f"FAIL  {rel}: a document is missing kind/apiVersion")
                break
        else:
            print(f"ok    {rel}")
    print(f"\n{len(files)} files, {docs} documents, {failed} problem(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
