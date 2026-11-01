"""Шаг B1: разобрать две строки MRZ на train.

Формат — TD3 (2×44). Для внутреннего паспорта РФ номер в MRZ такой:
позиции 1-3 — первые три цифры серии, 4-9 — шесть цифр номера;
четвёртая цифра серии лежит в optional[0], дальше YYMMDD выдачи и код без дефиса.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRED_PATH = ROOT / "docs" / "data" / "train_predictions.jsonl"
OUT_JSONL = ROOT / "analysis" / "mrz" / "train_mrz_parse.jsonl"
OUT_SUMMARY = ROOT / "analysis" / "mrz" / "train_mrz_parse_summary.json"
OUT_TXT = ROOT / "analysis" / "mrz" / "train_mrz_parse_summary.txt"

WEIGHTS = (7, 3, 1)


def load_jsonl(path):
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def char_value(ch):
    if ch == "<":
        return 0
    if "0" <= ch <= "9":
        return ord(ch) - ord("0")
    if "A" <= ch <= "Z":
        return ord(ch) - ord("A") + 10
    return None


def check_digit(text):
    total = 0
    for i, ch in enumerate(text):
        value = char_value(ch)
        if value is None:
            return None
        total += value * WEIGHTS[i % 3]
    return str(total % 10)


def check_ok(expected, calculated):
    if calculated is None:
        return False
    if expected == calculated:
        return True
    # в ICAO «<» в позиции контрольной цифры эквивалентен 0
    return expected == "<" and calculated == "0"


def yyymmdd_to_dots(yymmdd):
    if len(yymmdd) != 6 or any(ch == "<" for ch in yymmdd):
        return None
    if not yymmdd.isdigit():
        return None
    yy, mm, dd = yymmdd[:2], yymmdd[2:4], yymmdd[4:6]
    year = int(yy)
    # для паспортов РФ разумный диапазон: 1900–2099 через порог 30
    century = 1900 if year >= 30 else 2000
    return f"{dd}.{mm}.{century + year}"


def format_licence(digits10):
    if len(digits10) != 10 or not digits10.isdigit():
        return None
    return f"{digits10[:2]} {digits10[2:4]} {digits10[4:]}"


def format_code(digits6):
    if len(digits6) != 6 or not digits6.isdigit():
        return None
    return f"{digits6[:3]}-{digits6[3:]}"


def sex_ru(ch):
    if ch == "M":
        return "МУЖ."
    if ch == "F":
        return "ЖЕН."
    return None


def parse_names(line1):
    # positions 6..44 (index 5:)
    raw = line1[5:].rstrip("<")
    parts = [p for p in raw.split("<") if p]
    last = parts[0] if len(parts) > 0 else None
    first = parts[1] if len(parts) > 1 else None
    middle = parts[2] if len(parts) > 2 else None
    return {
        "last_name_latin": last,
        "first_name_latin": first,
        "middle_name_latin": middle,
    }


def parse_mrz(lines):
    result = {
        "present": False,
        "n_lines": 0,
        "line_lengths": [],
        "line_texts": [],
        "length_ok": False,
        "checks": {},
        "checks_ok": False,
        "fields": {},
        "issues": [],
    }
    if not lines:
        result["issues"].append("mrz_null")
        return result

    texts = [(line.get("text") or "") for line in lines]
    result["present"] = True
    result["n_lines"] = len(texts)
    result["line_lengths"] = [len(t) for t in texts]
    result["line_texts"] = texts

    if len(texts) != 2:
        result["issues"].append(f"expected_2_lines_got_{len(texts)}")
        return result

    line1, line2 = texts
    if len(line1) != 44 or len(line2) != 44:
        result["issues"].append("bad_length")
        return result
    result["length_ok"] = True

    # line1 structural bits
    doc_type = line1[0:2]
    country1 = line1[2:5]
    names = parse_names(line1)
    if doc_type not in ("PN", "P<", "PD"):
        result["issues"].append(f"unusual_doc_type_{doc_type}")
    if country1 != "RUS":
        result["issues"].append(f"line1_country_{country1}")

    # line2 fields
    number9 = line2[0:9]
    number_cd = line2[9]
    nationality = line2[10:13]
    birth = line2[13:19]
    birth_cd = line2[19]
    sex = line2[20]
    expiry = line2[21:27]
    expiry_cd = line2[27]
    optional = line2[28:42]
    optional_cd = line2[42]
    composite_cd = line2[43]

    calc = {
        "number": check_digit(number9),
        "birth": check_digit(birth),
        "expiry": check_digit(expiry),
        "optional": check_digit(optional),
        "composite": check_digit(line2[0:10] + line2[13:20] + line2[21:43]),
    }
    checks = {
        "number": {
            "expected": number_cd,
            "calculated": calc["number"],
            "ok": check_ok(number_cd, calc["number"]),
        },
        "birth": {
            "expected": birth_cd,
            "calculated": calc["birth"],
            "ok": check_ok(birth_cd, calc["birth"]),
        },
        "expiry": {
            "expected": expiry_cd,
            "calculated": calc["expiry"],
            "ok": check_ok(expiry_cd, calc["expiry"]),
        },
        "optional": {
            "expected": optional_cd,
            "calculated": calc["optional"],
            "ok": check_ok(optional_cd, calc["optional"]),
        },
        "composite": {
            "expected": composite_cd,
            "calculated": calc["composite"],
            "ok": check_ok(composite_cd, calc["composite"]),
        },
    }
    result["checks"] = checks
    result["checks_ok"] = all(item["ok"] for item in checks.values())
    for name, item in checks.items():
        if not item["ok"]:
            result["issues"].append(f"check_fail_{name}")

    if nationality != "RUS":
        result["issues"].append(f"nationality_{nationality}")

    # RU optional: 4th series digit + issue YYMMDD + code NNNNNN + filler
    series_digit4 = optional[0]
    issue_raw = optional[1:7]
    code_raw = optional[7:13]
    if series_digit4.isdigit() and number9.isdigit():
        series4 = number9[:3] + series_digit4
        number6 = number9[3:9]
        number10 = series4 + number6
    else:
        series4 = None
        number6 = None
        number10 = None

    fields = {
        **names,
        "doc_type": doc_type,
        "issuing_country": country1,
        "nationality": nationality,
        "series_raw": series4,
        "number_raw": number6,
        "licence_number_digits": number10,
        "Licence_number": format_licence(number10) if number10 else None,
        "Birth_date": yyymmdd_to_dots(birth),
        "birth_raw": birth,
        "Sex_ru": sex_ru(sex),
        "sex_raw": sex,
        "expiry_raw": expiry,
        "Issue_date": yyymmdd_to_dots(issue_raw),
        "issue_raw": issue_raw,
        "Issue_organisation_code": format_code(code_raw),
        "code_raw": code_raw,
        "optional_raw": optional,
    }
    result["fields"] = fields
    return result


def main():
    preds = load_jsonl(PRED_PATH)
    n = len(preds)
    n_null = 0
    n_len_ok = 0
    n_checks_ok = 0
    n_parsed_fields = 0
    check_fail_counts = {
        "number": 0,
        "birth": 0,
        "expiry": 0,
        "optional": 0,
        "composite": 0,
    }
    length_hist = {}

    with OUT_JSONL.open("w", encoding="utf-8") as fh:
        for pred in preds:
            parsed = parse_mrz((pred.get("mrz") or {}).get("lines"))
            row = {
                "doc_id": pred["doc_id"],
                "source": pred.get("source"),
                **parsed,
            }
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

            if not parsed["present"]:
                n_null += 1
            key = tuple(parsed["line_lengths"])
            length_hist[str(key)] = length_hist.get(str(key), 0) + 1
            if parsed["length_ok"]:
                n_len_ok += 1
            if parsed["checks_ok"]:
                n_checks_ok += 1
            if parsed["length_ok"]:
                for name, item in parsed["checks"].items():
                    if not item["ok"]:
                        check_fail_counts[name] += 1
            # поле считаем «достатым», если ключевые значения не None
            f = parsed.get("fields") or {}
            if f.get("Licence_number") and f.get("Birth_date") and f.get("Sex_ru"):
                n_parsed_fields += 1

    summary = {
        "documents": n,
        "mrz_null": n_null,
        "mrz_present": n - n_null,
        "length_44x2": n_len_ok,
        "all_checks_ok": n_checks_ok,
        "core_fields_extracted": n_parsed_fields,
        "check_fail_counts_among_length_ok": check_fail_counts,
        "line_length_histogram": dict(
            sorted(length_hist.items(), key=lambda kv: -kv[1])
        ),
    }
    OUT_SUMMARY.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        f"документов: {n}",
        f"mrz null: {n_null}",
        f"mrz есть: {n - n_null}",
        f"длина ровно 44x2: {n_len_ok}",
        f"все контрольные цифры ок: {n_checks_ok}",
        f"достаты номер+дата рождения+пол: {n_parsed_fields}",
        "провалы проверок среди length_ok:",
    ]
    for name, cnt in check_fail_counts.items():
        lines.append(f"  {name}: {cnt}")
    lines.append("гистограмма длин строк:")
    for key, cnt in summary["line_length_histogram"].items():
        lines.append(f"  {key}: {cnt}")
    text = "\n".join(lines) + "\n"
    OUT_TXT.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
