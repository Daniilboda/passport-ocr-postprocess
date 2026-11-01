"""Постобработка OCR паспорта РФ: accept/reject + поля.

Использование:
  python postprocess.py <predictions.jsonl> <answers.jsonl>

CPU, без интернета. Правило — analysis/accept/decide_document.py (C4).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "analysis" / "accept"))

from decide_document import decide_document  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2:
        print(
            "usage: python postprocess.py <predictions.jsonl> <answers.jsonl>",
            file=sys.stderr,
        )
        return 2

    pred_path = Path(args[0])
    out_path = Path(args[1])
    if not pred_path.is_file():
        print(f"нет файла: {pred_path}", file=sys.stderr)
        return 1

    n_accept = n_reject = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pred_path.open(encoding="utf-8") as fin, out_path.open(
        "w", encoding="utf-8"
    ) as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            pred = json.loads(line)
            decision = decide_document(pred)
            if decision["decision"] == "accept":
                n_accept += 1
                row = {
                    "doc_id": decision["doc_id"],
                    "decision": "accept",
                    "fields": decision["fields"],
                }
            else:
                n_reject += 1
                row = {"doc_id": decision["doc_id"], "decision": "reject"}
            fout.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"docs={n_accept + n_reject} accept={n_accept} reject={n_reject} -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
