import os
import sys

PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(PACKAGE_ROOT))
sys.path.insert(0, PACKAGE_ROOT)


def repo_path(*parts):
    return os.path.join(REPO, *parts)
