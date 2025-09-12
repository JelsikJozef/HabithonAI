# Automatically add the monorepo src directory to sys.path for tests and scripts.
import os
import sys

root = os.path.dirname(__file__)
src = os.path.join(root, "src")
if src not in sys.path:
    sys.path.insert(0, src)
