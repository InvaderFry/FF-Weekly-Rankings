"""Compatibility validation for GitHub's queue key until actionlint supports it."""
from pathlib import Path
import yaml


def check_queue(document):
    if isinstance(document, dict):
        concurrency = document.get("concurrency")
        if isinstance(concurrency, dict) and "queue" in concurrency:
            if concurrency["queue"] not in ("single", "max"):
                raise ValueError("concurrency.queue must be single or max")
            if concurrency["queue"] == "max" and concurrency.get("cancel-in-progress") is not False:
                raise ValueError("queue: max requires cancel-in-progress: false")
        for value in document.values():
            check_queue(value)
    elif isinstance(document, list):
        for value in document:
            check_queue(value)


if __name__ == "__main__":
    for path in Path(".github/workflows").glob("*.y*ml"):
        check_queue(yaml.safe_load(path.read_text()))
