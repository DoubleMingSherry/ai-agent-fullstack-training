"""Run the same small question set against both retrieval strategies."""
import argparse
import subprocess
import sys
from evaluate import GOLD, ROOT

p = argparse.ArgumentParser()
p.add_argument("--answer", action="store_true", help="also call the real streaming LLM")
a = p.parse_args()
for mode in ("dense", "hybrid"):
    for question in GOLD:
        cmd = [sys.executable, str(ROOT / "rag.py"), "--mode", mode, "--question", question]
        if not a.answer:
            cmd.append("--retrieve-only")
        subprocess.run(cmd, check=True)
