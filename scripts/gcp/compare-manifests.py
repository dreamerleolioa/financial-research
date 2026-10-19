"""Compare a frozen source manifest with an idle, freshly restored target."""
import argparse
import json
from pathlib import Path
import re


# PostgreSQL reparses a varchar[] -> text[] coercion as per-element coercions
# during pg_dump restore. Normalize only this exact ASCII enum literal form;
# retain every value, operator, name and validation flag. Other differences fail.
_ENUM_LITERAL = r"'[A-Za-z0-9_]+'::character varying"
_ENUM_ARRAY = re.compile(r"\(\(ARRAY\[(" + _ENUM_LITERAL + r"(?:, " + _ENUM_LITERAL + r")*)\]\)::text\[\]\)")


def normalize_constraints(items):
    def replace(match):
        return "(ARRAY[" + ", ".join(f"({item})::text" for item in match[1].split(", ")) + "])"
    return [[name, _ENUM_ARRAY.sub(replace, definition), validated]
            for name, definition, validated in items]


def read_manifest(path: Path) -> dict:
    result = {}
    for line in path.read_text().splitlines():
        item = json.loads(line)
        name = item["table"]
        if name in result:
            raise ValueError(f"Duplicate table: {name}")
        result[name] = item
    if not result:
        raise ValueError("Empty manifest")
    return result


def compare(source: dict, target: dict) -> list[str]:
    differences = []
    for name in sorted(source.keys() | target.keys()):
        if name not in source or name not in target:
            differences.append(f"{name}: missing or unexpected table")
            continue
        for field in ("rows", "content_md5", "columns", "constraints", "indexes", "sequences"):
            left, right = source[name][field], target[name][field]
            if field == "constraints":
                left, right = normalize_constraints(left), normalize_constraints(right)
            if left != right:
                differences.append(f"{name}: {field} differs")
        # Financial v1 manifests were exported with a preflight that refused
        # all user triggers and custom dependencies, hence these known defaults.
        for field, default in (("triggers", []), ("schema_objects", None)):
            if source[name].get(field, default) != target[name].get(field, default):
                differences.append(f"{name}: {field} differs")
    return differences


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    differences = compare(read_manifest(args.source), read_manifest(args.target))
    if differences:
        raise SystemExit("\n".join(differences))
    print("All table counts, content hashes, columns, constraints and indexes match.")
