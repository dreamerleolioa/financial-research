from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ai_stock_sentinel.analysis.news_cleaner import FinancialNewsCleaner


def read_input(file_path: str | None, inline_text: str | None) -> str:
    if inline_text:
        return inline_text
    if file_path:
        with open(file_path, "r", encoding="utf-8") as file:
            return file.read()
    if not sys.stdin.isatty():
        return sys.stdin.read()

    print("請貼上網頁內容，完成後按 Ctrl-D：")
    return sys.stdin.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Financial news cleaner agent")
    parser.add_argument("--file", type=str, help="Input text file path")
    parser.add_argument("--text", type=str, help="Inline text content")
    parser.add_argument("--model", type=str, default="gpt-4o-mini")
    args = parser.parse_args()

    content = read_input(args.file, args.text)
    cleaner = FinancialNewsCleaner(model=args.model)
    cleaned = cleaner.clean(content)
    print(json.dumps(cleaned.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
