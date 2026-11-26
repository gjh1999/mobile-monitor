import json
from pathlib import Path


def pages(rows, page_size):
    ordered = sorted(rows, key=lambda r: (r["updated"], r["id"]))
    cursor = None
    while True:
        page = [r for r in ordered if cursor is None or r["updated"] > cursor][:page_size]
        if not page:
            return
        yield page
        cursor = page[-1]["updated"]


def export(rows, destination, page_size=100):
    destination = Path(destination)
    with destination.open("w", encoding="utf-8") as stream:
        for page in pages(rows, page_size):
            visible = [r for r in page if not r["archived"]]
            if not visible:
                continue
            for row in visible:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
