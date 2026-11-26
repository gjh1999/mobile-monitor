from pathlib import Path

readings = Path(__file__).parent / "data" / "readings.txt"
values = [int(line) for line in readings.read_text(encoding="utf-8").splitlines()]
print(sum(values) / len(values))
