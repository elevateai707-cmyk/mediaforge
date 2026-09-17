import json
import os
import tempfile
from pathlib import Path

from .. import config, persist

SECRET_FILE = config.DATA_DIR / "provider-secrets.json"


def key(provider):
    env = os.environ.get(
        "COMFY_API_KEY" if provider == "comfy" else "ELEVENLABS_API_KEY"
    )
    if env:
        return env
    try:
        return json.loads(SECRET_FILE.read_text()).get(provider, "")
    except (OSError, ValueError):
        return ""


def set_key(provider, value):
    if provider not in ("comfy", "elevenlabs"):
        raise ValueError("Unknown provider")
    try:
        existing = json.loads(SECRET_FILE.read_text())
    except (OSError, ValueError):
        existing = {}
    existing[provider] = value
    SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(dir=SECRET_FILE.parent, prefix=".keys-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(existing, f)
        os.replace(path, SECRET_FILE)
    finally:
        Path(path).unlink(missing_ok=True)


def configuration():
    return json.loads(persist.get_setting("provider_configuration", "{}"))


def save_configuration(data):
    persist.set_setting("provider_configuration", json.dumps(data))
