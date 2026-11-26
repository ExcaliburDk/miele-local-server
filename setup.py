"""Setup configuration for Miele Local."""

from pathlib import Path
import re
from setuptools import setup


def get_version() -> str:
    version_file = Path(__file__).parent / "miele_local" / "__version__.py"
    content = version_file.read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*[\'"]([^\'"]+)[\'"]', content, re.MULTILINE)
    if match:
        return match.group(1)
    raise RuntimeError("Unable to find __version__ in miele_local/__version__.py")


setup(version=get_version())
