import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_settings(path=None):
    p = Path(path) if path else ROOT / "config" / "settings.json"
    return json.loads(p.read_text())


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), default=_default))


def _default(o):
    import numpy as np
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        f = float(o)
        return None if f != f else round(f, 6)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, datetime):
        return iso(o)
    raise TypeError(f"not serializable: {type(o)}")
