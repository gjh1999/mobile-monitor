import json
from pathlib import Path


def export(rows, destination, page_size=100):
    destination = Path(destination)
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            for row in sorted(rows, key=lambda r: (r["updated"], r["id"])):
                if not row["archived"]:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
