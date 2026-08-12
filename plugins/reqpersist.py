# ==============================================================================
#  Cipher Elite - Permanent Dependency Persistence
#
#  Plugin Name:    reqpersist
#  Version:        1.0.0
#  Target path:    plugins/reqpersist.py
#
#  What it does:
#  plugins/install.py already auto-installs a missing package the FIRST time
#  you `.install` a plugin that needs it - but that install only lives in the
#  current venv. If the server ever gets redeployed / the venv gets rebuilt
#  from `requirements.txt` (fresh `pip install -r requirements.txt`), that
#  package is missing again and you'd have to re-`.install` the plugin.
#
#  This plugin hooks into install.py's installer (no need to edit install.py
#  itself) so that every package it successfully installs is ALSO appended
#  to requirements.txt - so it survives forever, across any future redeploy.
#
#  No new commands - it's a silent, automatic hook. Just drop this file in
#  plugins/ and restart the bot once; from then on every `.install` that
#  needs a new package will permanently remember it.
# ==============================================================================

import re
from pathlib import Path

import plugins.install as install_mod

VERSION = "1.0.0"
CATEGORY = "developer"

REQUIREMENTS_PATH = Path(__file__).parent.parent / "requirements.txt"

# Keep a reference to the ORIGINAL installer before we wrap it, in case this
# plugin ever gets reloaded (so we don't wrap our own wrapper twice).
_original_install_package = getattr(install_mod, "_reqpersist_original", None) or install_mod.install_package
install_mod._reqpersist_original = _original_install_package


def _package_base_name(line: str) -> str:
    """'requests==2.31.0' -> 'requests', 'qrcode[pil]' -> 'qrcode'."""
    return re.split(r"[<>=!\[; ]", line.strip(), 1)[0].strip().lower()


def _read_requirements():
    if not REQUIREMENTS_PATH.exists():
        return []
    return [
        line.strip()
        for line in REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _persist_requirement(pip_name: str):
    """Add pip_name to requirements.txt permanently, unless a matching
    package (any version/extras) is already listed there."""
    try:
        existing = _read_requirements()
        existing_bases = {_package_base_name(line) for line in existing}
        if _package_base_name(pip_name) in existing_bases:
            return  # already tracked in requirements.txt, nothing to do

        with open(REQUIREMENTS_PATH, "a", encoding="utf-8") as f:
            if existing and not existing[-1].endswith("\n"):
                f.write("\n")
            f.write(f"{pip_name}\n")
    except Exception:
        pass  # never let a requirements.txt write error break `.install`


async def _patched_install_package(import_name):
    """Same as install.py's install_package(), plus: on success, permanently
    remember the package in requirements.txt."""
    success, err = await _original_install_package(import_name)
    if success:
        pip_name = install_mod.PACKAGE_MAPPING.get(import_name, import_name)
        _persist_requirement(pip_name)
    return success, err


def init(client_instance):
    # No commands to register - this plugin only patches install.py's
    # installer function so it needs to appear in the plugin list too.
    pass


async def register_commands():
    install_mod.install_package = _patched_install_package
