"""Шаг C4: правило accept/reject для одного документа.

Собрать 10 полей из viz + MRZ (политика B3), затем жёстко резать до нуля
ложных accept на train:

- нужен MRZ с checks_ok; restore-поля только из MRZ;
- ФИО: viz + обязательное согласие с латиницей MRZ (все три имени);
- mobile_v2: reject (char_conf часто = 1.0, порог некалиброван);
- min char_conf: ФИО / место / кем выдан >= 0.95;
- структурные признаки + эвристики неполноты Birth_place / Issue_organization
  (короткое место без региона; org короче варианта из subdivisions.csv);
- формат + действительность на 01.10.2026.

Справочник subdivisions: сигнал неполноты org по коду (не exact-match названия).
YY серии — не hard reject (вывод C2).
"""
from __future__ import annotations

import csv
import importlib.util
import re
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "analysis" / "accept") not in sys.path:
    sys.path.insert(0, str(ROOT / "analysis" / "accept"))

from passport_validity import check_validity  # noqa: E402

FIELDS = [
    "Last_name_ru",
    "First_name_ru",
    "Middle_name_ru",
    "Sex_ru",
    "Birth_date",
    "Birth_place_ru",
    "Issue_date",
    "Issue_organisation_code",
    "Licence_number",
    "Issue_organization_ru",
]

RESTORE_FROM_MRZ = {
    "Licence_number",
    "Sex_ru",
    "Birth_date",
    "Issue_date",
    "Issue_organisation_code",
}

NAME_FIELDS = {
    "Last_name_ru": "last_name_latin",
    "First_name_ru": "first_name_latin",
    "Middle_name_ru": "middle_name_latin",
}

# C4: общий порог 0.95 (график + incompleteness → 0 bad на train).
# Буквенные OCR-ошибки режет min_char; обрезки — place/org incomplete.
MIN_CHAR_NAME = 0.95
MIN_CHAR_PLACE = 0.95
REJECT_SOURCES = {"mobile_v2"}
SUBDIV_PATH = ROOT / "docs" / "reference" / "subdivisions.csv"

CLERK = {
    "А": "A",
    "Б": "B",
    "В": "V",
    "Г": "G",
    "Д": "D",
    "Е": "E",
    "Ё": "2",
    "Ж": "J",
    "З": "Z",
    "И": "I",
    "Й": "Q",
    "К": "K",
    "Л": "L",
    "М": "M",
    "Н": "N",
    "О": "O",
    "П": "P",
    "Р": "R",
    "С": "S",
    "Т": "T",
    "У": "U",
    "Ф": "F",
    "Х": "H",
    "Ц": "C",
    "Ч": "3",
    "Ш": "4",
    "Щ": "W",
    "Ъ": "X",
    "Ы": "Y",
    "Ь": "9",
    "Э": "6",
    "Ю": "7",
    "Я": "8",
}

RE_CODE = re.compile(r"^\d{3}-\d{3}$")
RE_NUM = re.compile(r"^\d{2} \d{2} \d{6}$")
RE_DATE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
RE_SEX = re.compile(r"^(МУЖ|ЖЕН)\.$")

RE_SETTLE = re.compile(
    r"(^|\s)(Г|С|Д|П|Х|СТ|ДЕР|ПОС|ГОР|ПГТ|АУЛ|ГОРОД|СЕЛО|ДЕРЕВНЯ|ПОСЕЛОК)\.?",
    re.I,
)
RE_PLACE_START_MID = re.compile(
    r"^(Р-НА|Р-НЕ|РАЙОНА|РАЙОНЕ|ОБЛ\.|ОБЛАСТИ)\b"
)
RE_PLACE_START_GENITIVE = re.compile(
    r"^[А-ЯЁ]{4,}(ОГО|ОЙ|ЕГО|ЕЙ|СКОГО|СКОЙ)\s"
)
RE_ADJ_END = re.compile(
    r"(СКОГО|СКОЙ|СКИЙ|ЦКОГО|ЦКОЙ|НОГО|НОЙ|ОВОГО|ОВОЙ|ЕНСКОГО|ЕНСКОЙ)$"
)
RE_SOFT_SPACE = re.compile(
    r"\b(ОБЛ|РЕСП|КР|РАЙОН|Р-Н|Р-НА|Р-НЕ)\.(\S)", re.I
)

# Неполнота place: маркер НП есть, региона нет, текст короткий.
# Регион без города / город с регионом — ок (не требуем оба).
RE_PLACE_REGION = re.compile(
    r"(ОБЛ\.?|ОБЛАСТ|КР\.?|КРА[ЙЯЮЕ]|РЕСП|АО\b|ОКРУГ)",
    re.I,
)
RE_PLACE_SETTLE_MARK = re.compile(
    r"(^|\s)(Г\.|ГОР\.|ГОРОД|С\.|СЕЛО|П\.|ПОС\.|ПОСЕЛОК|ДЕР\.|ДЕРЕВНЯ|"
    r"АУЛ|Х\.|ХУТ\.|ХУТОР|РП\.|ПГТ)",
    re.I,
)
PLACE_SHORT_NO_REGION_LEN = 15


def _load_parse_mrz():
    path = ROOT / "analysis" / "mrz" / "parse_train_mrz.py"
    spec = importlib.util.spec_from_file_location("parse_train_mrz", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.parse_mrz


parse_mrz = _load_parse_mrz()


def norm(value: str) -> str:
    return " ".join(str(value or "").split()).upper()


def to_clerk(text: str) -> str | None:
    out = []
    for ch in norm(text):
        if ch == " ":
            continue
        if ch in CLERK:
            out.append(CLERK[ch])
        elif "A" <= ch <= "Z" or "0" <= ch <= "9":
            out.append(ch)
    return "".join(out) or None


def viz_field_text(field) -> str | None:
    if not field or not field.get("lines"):
        return None
    parts = [line.get("text") or "" for line in field["lines"]]
    if not any(part.strip() for part in parts):
        return None
    return " ".join(parts)


def viz_min_char(field) -> float | None:
    if not field or not field.get("lines"):
        return None
    vals = []
    for line in field["lines"]:
        vals.extend(line.get("char_conf") or [])
    return min(vals) if vals else None


def soft_space_region(text: str) -> str:
    """Пробел после ОБЛ./РЕСП. и т.п., если OCR слипся (ОБЛ.В → ОБЛ. В)."""
    s = " ".join(str(text or "").split())
    return RE_SOFT_SPACE.sub(r"\1. \2", s)


@lru_cache(maxsize=1)
def load_subdivisions() -> dict[str, tuple[str, ...]]:
    """code -> варианты названий из DaData (может быть несколько на код)."""
    by: dict[str, list[str]] = {}
    if not SUBDIV_PATH.is_file():
        return {}
    with SUBDIV_PATH.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = (row.get("code") or "").strip()
            name = (row.get("name") or "").strip()
            if not code or not name:
                continue
            by.setdefault(code, [])
            if name not in by[code]:
                by[code].append(name)
    return {k: tuple(v) for k, v in by.items()}


def fold_org_for_compare(text: str) -> str:
    """Схлопывание падежей/синонимов для сравнения длины с subdivisions."""
    s = (text or "").upper().replace("Ё", "Е")
    s = re.sub(r"ОУФМС", "ОТДЕЛУФМС", s)
    s = re.sub(r"ОТДЕЛЕНИЕМ|ОТДЕЛЕНИЕ|ОТДЕЛОМ|ОТДЕЛА|ОТДЕЛ", "ОТДЕЛ", s)
    s = re.sub(r"УПРАВЛЕНИЕМ|УПРАВЛЕНИЕ", "УПРАВЛЕНИЕ", s)
    s = re.sub(r"ХАНТЫ\s*-\s*МАНСИЙСК\w*\s*АО\s*-?\s*ЮГР\w*", "ХМАОЮГРА", s)
    s = re.sub(r"ХМАО\s*-?\s*ЮГР\w*", "ХМАОЮГРА", s)
    s = re.sub(r"ХМАО", "ХМАОЮГРА", s)
    s = re.sub(r"ОБЛ\.?|ОБЛАСТИ|ОБЛАСТЬ", "ОБЛ", s)
    s = re.sub(r"КРАЯ|КРАЮ|КРАЕМ|КРАЙ", "КРАЙ", s)
    s = re.sub(r"РЕСП\.?|РЕСПУБЛИКИ|РЕСПУБЛИКЕ|РЕСПУБЛИКА", "РЕСП", s)
    s = re.sub(r"РАЙОНЕ|РАЙОНА|РАЙОН|Р-НЕ|Р-НА|Р-Н", "РАЙОН", s)
    return re.sub(r"[^0-9А-ЯA-Z]+", "", s)


def place_incomplete(text: str) -> bool:
    """Короткое «только НП» без региона — типичная обрезка детектора.

    Не требуем одновременно город и область: регион без НП или длинный
    топоним без маркера региона — допустимы.
    """
    t = " ".join(str(text or "").split())
    if not t:
        return True
    has_region = bool(RE_PLACE_REGION.search(t))
    has_settle = bool(RE_PLACE_SETTLE_MARK.search(t))
    if has_settle and not has_region and len(t) <= PLACE_SHORT_NO_REGION_LEN:
        return True
    return False


def org_incomplete(text: str, code: str | None) -> bool:
    """Неполный Issue_organization_ru относительно subdivisions / синтаксиса."""
    t = " ".join(str(text or "").split())
    if not t:
        return True

    # УФМС РОССИИ <район> без «ПО …» — выпала середина
    if re.search(r"УФМС РОССИИ\s+[А-ЯЁ]", t, re.I) and not re.search(
        r"УФМС РОССИИ ПО\b", t, re.I
    ):
        return True
    # ОМВД … без «(РОССИИ) ПО» — дырка в середине (…ОМВД ЗМИЕВКА)
    # или OCR сломал РОССИИ/ПО; на штампе у полного названия обычно есть ПО.
    if re.search(r"\bОМВД\b", t, re.I) and not re.search(
        r"ОМВД(?:\s+РОССИИ)?\s+ПО\b", t, re.I
    ):
        return True

    if not code:
        return False
    variants = load_subdivisions().get(code) or ()
    if not variants:
        return False

    folded = fold_org_for_compare(t)
    if not folded:
        return True
    for var in variants:
        fv = fold_org_for_compare(var)
        # viz — префикс более длинного варианта справочника → обрезан хвост
        if len(fv) >= len(folded) + 8 and fv.startswith(folded):
            return True
    return False


def place_struct_ok(text: str) -> bool:
    t = norm(text)
    if not t:
        return False
    if RE_PLACE_START_MID.match(t) or RE_PLACE_START_GENITIVE.match(t):
        return False
    if RE_ADJ_END.search(t):
        return False
    if t.endswith((" В", " ПО", " И", " НА")):
        return False
    # только «ПЕНЗЕНСКОЙ ОБЛ.» без населённого пункта — обрезок
    if not RE_SETTLE.search(t) and re.search(
        r"(ОБЛ\.?|ОБЛАСТИ|КР\.?|КРАЯ|КРАЙ|РЕСП\.?)$", t
    ):
        return False
    return True


def org_struct_ok(text: str) -> bool:
    t = norm(text)
    if not t:
        return False
    if re.match(r"^(РОССИИ ПО|ПО |В )\b", t):
        return False
    if t.endswith((" В", " ПО", " И", " НА", " ОТ")):
        return False
    if re.search(r"(СКОГО|СКОЙ|СКИЙ|НОГО|НОЙ|ОВОГО|ОВОЙ)$", t):
        return False
    return True


def format_issues(fields: dict) -> list[str]:
    issues = []
    if not RE_SEX.match(fields.get("Sex_ru") or ""):
        issues.append("bad_sex")
    for key, tag in (("Birth_date", "bad_birth_date"), ("Issue_date", "bad_issue_date")):
        if not RE_DATE.match(fields.get(key) or ""):
            issues.append(tag)
    if not RE_CODE.match(fields.get("Issue_organisation_code") or ""):
        issues.append("bad_code_format")
    if not RE_NUM.match(fields.get("Licence_number") or ""):
        issues.append("bad_licence_format")
    for key in (
        "Last_name_ru",
        "First_name_ru",
        "Middle_name_ru",
        "Birth_place_ru",
        "Issue_organization_ru",
    ):
        if not norm(fields.get(key) or ""):
            issues.append(f"empty_{key}")
    return issues


def decide_document(pred: dict) -> dict:
    """Возвращает ответ answers.jsonl + reasons."""
    reasons: list[str] = []
    doc_id = pred["doc_id"]

    if pred.get("source") in REJECT_SOURCES:
        return {
            "doc_id": doc_id,
            "decision": "reject",
            "reasons": ["bad_source:mobile_v2"],
            "source": {},
            "mrz_ok": False,
        }

    viz = pred.get("viz") or {}
    mrz = parse_mrz((pred.get("mrz") or {}).get("lines"))
    mrz_ok = bool(mrz.get("length_ok") and mrz.get("checks_ok"))
    mrz_fields = mrz.get("fields") or {}

    if not mrz_ok:
        return {
            "doc_id": doc_id,
            "decision": "reject",
            "reasons": ["need_mrz"],
            "source": {},
            "mrz_ok": False,
        }

    assembled: dict[str, str] = {}
    source: dict[str, str] = {}

    # 1) restore: только MRZ (на C4 viz для этих полей не принимаем)
    for name in RESTORE_FROM_MRZ:
        mrz_val = mrz_fields.get(name)
        if mrz_val:
            assembled[name] = mrz_val
            source[name] = "mrz"
        else:
            reasons.append(f"missing_mrz:{name}")

    # 2) ФИО: viz + обязательный MRZ latin
    for name, latin_key in NAME_FIELDS.items():
        field = viz.get(name)
        text = viz_field_text(field)
        min_c = viz_min_char(field)
        if text is None:
            reasons.append(f"missing:{name}")
            continue
        if min_c is None or min_c < MIN_CHAR_NAME:
            reasons.append(f"low_char_conf:{name}")
            continue
        mrz_latin = mrz_fields.get(latin_key)
        viz_latin = to_clerk(text)
        if not mrz_latin:
            reasons.append(f"missing_mrz_name:{name}")
            continue
        if not viz_latin or norm(mrz_latin) != norm(viz_latin):
            reasons.append(f"name_conflict_mrz:{name}")
            continue
        assembled[name] = text
        source[name] = "viz"

    # 3) место / кем выдан — только viz; структура + неполнота
    for name, struct_ok in (
        ("Birth_place_ru", place_struct_ok),
        ("Issue_organization_ru", org_struct_ok),
    ):
        field = viz.get(name)
        text = viz_field_text(field)
        min_c = viz_min_char(field)
        if text is None:
            reasons.append(f"missing:{name}")
            continue
        if min_c is None or min_c < MIN_CHAR_PLACE:
            reasons.append(f"low_char_conf:{name}")
            continue
        text = soft_space_region(text)
        if not struct_ok(text):
            reasons.append(f"truncated:{name}")
            continue
        if name == "Birth_place_ru" and place_incomplete(text):
            reasons.append(f"incomplete:{name}")
            continue
        if name == "Issue_organization_ru" and org_incomplete(
            text, assembled.get("Issue_organisation_code")
        ):
            reasons.append(f"incomplete:{name}")
            continue
        assembled[name] = text
        source[name] = "viz"

    if reasons:
        return {
            "doc_id": doc_id,
            "decision": "reject",
            "reasons": reasons,
            "source": source,
            "mrz_ok": mrz_ok,
        }

    fmt = format_issues(assembled)
    if fmt:
        return {
            "doc_id": doc_id,
            "decision": "reject",
            "reasons": fmt,
            "source": source,
            "mrz_ok": mrz_ok,
            "fields_draft": assembled,
        }

    validity = check_validity(assembled["Birth_date"], assembled["Issue_date"])
    if not validity.valid:
        return {
            "doc_id": doc_id,
            "decision": "reject",
            "reasons": [f"invalid_passport:{validity.reason}"],
            "source": source,
            "mrz_ok": mrz_ok,
            "fields_draft": assembled,
        }

    return {
        "doc_id": doc_id,
        "decision": "accept",
        "fields": {name: assembled[name] for name in FIELDS},
        "reasons": ["ok"],
        "source": source,
        "mrz_ok": mrz_ok,
    }
