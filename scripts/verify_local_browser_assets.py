"""Read-only smoke check of the local service and its browser asset graph."""

import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from urllib.request import urlopen

BASE = "http://127.0.0.1:8010"


def fetch(path):
    target = urljoin(BASE, path)
    if urlsplit(target).netloc != urlsplit(BASE).netloc:
        raise SystemExit("STOP: unexpected nonlocal asset.")
    try:
        with urlopen(target, timeout=5) as response:
            if response.status != 200:
                raise SystemExit("STOP: local request failed.")
            return response.read().decode("utf-8"), response.headers
    except Exception:
        raise SystemExit(
            "STOP: local service or asset unavailable: " + urlsplit(target).path
        ) from None


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        candidate = (
            values.get("src") if tag == "script"
            else values.get("href") if tag == "link"
            else None
        )
        if candidate and candidate.startswith("/static/"):
            self.paths.add(candidate)


def main():
    text, _ = fetch("/api/v1/health")
    if json.loads(text) != {
        "status": "ok", "application": "shift-scheduling-app",
    }:
        raise SystemExit("STOP: unexpected application health response.")

    text, _ = fetch("/api/v1/ready")
    if json.loads(text) != {"status": "ready", "database": "ok"}:
        raise SystemExit("STOP: database readiness failed.")

    text, _ = fetch("/")
    parser = Assets()
    parser.feed(text)
    if not any(urlsplit(p).path.endswith(".js") for p in parser.paths):
        raise SystemExit("STOP: application script missing.")

    pending = list(parser.paths)
    checked = set()
    while pending:
        path = pending.pop()
        if path in checked:
            continue
        checked.add(path)
        source, headers = fetch(path)
        filename = urlsplit(path).path
        if filename.endswith(".js"):
            if "javascript" not in headers.get("Content-Type", ""):
                raise SystemExit("STOP: unexpected JavaScript content type.")
            imports = re.findall(
                r'''(?:\bfrom\s*|\bimport\s*)["']([^"']+)["']''',
                source,
            )
            for imported in imports:
                resolved = urljoin(urljoin(BASE, path), imported)
                parsed = urlsplit(resolved)
                if parsed.netloc != urlsplit(BASE).netloc:
                    raise SystemExit("STOP: unexpected external module.")
                if not parsed.path.startswith("/static/"):
                    raise SystemExit("STOP: unexpected module location.")
                pending.append(parsed.path + (
                    "?" + parsed.query if parsed.query else ""
                ))

    print("LOCAL_APPLICATION_AND_DATABASE_READINESS=PASS")
    print("BROWSER_ASSET_DELIVERY_AND_STATIC_MODULE_IMPORTS=PASS")
    print("ASSETS_CHECKED=" + str(len(checked)))
    print("JAVASCRIPT_EXECUTION=OWNER_BROWSER_ACCEPTANCE_REQUIRED")
    print("BUSINESS_DATA_WRITES=NONE")


if __name__ == "__main__":
    main()
