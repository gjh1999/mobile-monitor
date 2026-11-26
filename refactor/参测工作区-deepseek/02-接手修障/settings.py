import json


def load(path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8-sig"))
    return {"subscriptions": []}


def save(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
