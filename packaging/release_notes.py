"""Print the CHANGELOG section for one version (used as GitHub release notes)."""
import re
import sys
from pathlib import Path

version = sys.argv[1]
text = Path(__file__).resolve().parents[1].joinpath("CHANGELOG.md").read_text("utf-8")
m = re.search(rf"^## {re.escape(version)}\b.*?$(.*?)(?=^## |\Z)", text, re.S | re.M)
body = m.group(1).strip() if m else "See CHANGELOG.md."
print(body)
print("""
---
**Install (Windows 10/11):** download `splitroll-%s-setup.exe` below and run it.
It isn't code-signed yet, so SmartScreen may warn — click **More info → Run anyway**.
""" % version)
