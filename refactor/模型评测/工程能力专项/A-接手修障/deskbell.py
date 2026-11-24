import argparse
from pathlib import Path

from settings import load, save
from subscriptions import configure, status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("workspace")
    parser.add_argument("action", choices=["enable", "disable", "status"])
    args = parser.parse_args()
    data = load(args.config)
    if args.action == "status":
        print(status(data, args.workspace))
    elif configure(data, args.workspace, args.action == "enable"):
        save(args.config, data)


if __name__ == "__main__":
    main()
