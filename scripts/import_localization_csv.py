"""Import simplified localization CSVs; keep source and reference JSONL separate."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path

from localization_agent.data.csv_import import CsvImportError, import_localization_csv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="+", help="UTF-8 CSV files, in import order")
    parser.add_argument("--dataset-id", required=True, help="Dataset namespace for internal record IDs")
    parser.add_argument("--output", type=Path, required=True, help="New or empty output directory")
    parser.add_argument("--context-format", choices=("text", "json"), default="text")
    parser.add_argument("--source-language", default="en")
    parser.add_argument("--reference-language", default="zh-Hans")
    args = parser.parse_args()
    try:
        result = import_localization_csv(args.inputs, args.output, dataset_id=args.dataset_id,
            context_format=args.context_format, source_language=args.source_language,
            reference_language=args.reference_language)
    except (CsvImportError, OSError, ValueError) as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps({**asdict(result), "output_dir": str(result.output_dir)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
