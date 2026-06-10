import json
from pathlib import Path

_SAMESITE_MAP = {"no_restriction": "None", "lax": "Lax", "strict": "Strict",
                 "none": "None", "unspecified": "Lax"}


def load(path: Path) -> list[dict]:
    """Load cookies from a Cookie-Editor style JSON export, normalise for Playwright."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for c in raw:
        cookie = {
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", ".baidu.com"),
            "path": c.get("path", "/"),
            "httpOnly": bool(c.get("httpOnly", False)),
            "secure": bool(c.get("secure", False)),
        }
        ss = (c.get("sameSite") or "Lax")
        cookie["sameSite"] = _SAMESITE_MAP.get(str(ss).lower(), ss if ss in ("Lax", "Strict", "None") else "Lax")
        exp = c.get("expirationDate") or c.get("expires")
        if exp and exp != -1:
            cookie["expires"] = int(exp)
        out.append(cookie)
    return out
