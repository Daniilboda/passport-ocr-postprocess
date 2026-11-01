"""Шаг C1: действительность паспорта РФ на дату оценки.

Положение о паспорте (ПП РФ от 23.12.2023 № 2267, п. 24):
- от 14 до 20 лет;
- от 20 до 45 лет;
- от 45 лет — бессрочно;
- после достижения 20 или 45 лет старый паспорт ещё действителен
  до оформления нового, но не более 90 календарных дней после дня рождения.

Дата оценки в задании: 01.10.2026.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Optional

AS_OF = date(2026, 10, 1)
GRACE_DAYS = 90


def parse_ru_date(text: str) -> Optional[date]:
    text = " ".join(str(text or "").split())
    try:
        return datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        return None


def age_on(day: date, birth: date) -> int:
    years = day.year - birth.year
    if (day.month, day.day) < (birth.month, birth.day):
        years -= 1
    return years


def add_years(birth: date, years: int) -> date:
    try:
        return birth.replace(year=birth.year + years)
    except ValueError:
        # 29 февраля → 28 февраля в невисокосном году
        return birth.replace(month=2, day=28, year=birth.year + years)


@dataclass
class ValidityResult:
    valid: bool
    reason: str
    birth: str | None = None
    issue: str | None = None
    age_at_issue: int | None = None
    regime: str | None = None
    expires_on: str | None = None  # последний день действия; None = бессрочно


def check_validity(
    birth_text: str,
    issue_text: str,
    as_of: date = AS_OF,
    grace_days: int = GRACE_DAYS,
) -> ValidityResult:
    birth = parse_ru_date(birth_text)
    issue = parse_ru_date(issue_text)
    if birth is None:
        return ValidityResult(False, "bad_birth_date", birth=birth_text, issue=issue_text)
    if issue is None:
        return ValidityResult(False, "bad_issue_date", birth=birth_text, issue=issue_text)
    if issue > as_of:
        return ValidityResult(
            False,
            "issue_after_as_of",
            birth=birth.isoformat(),
            issue=issue.isoformat(),
        )
    if issue < birth:
        return ValidityResult(
            False,
            "issue_before_birth",
            birth=birth.isoformat(),
            issue=issue.isoformat(),
        )

    age_issue = age_on(issue, birth)
    if age_issue < 0:
        return ValidityResult(False, "issue_before_birth", birth=birth.isoformat(), issue=issue.isoformat())

    if age_issue < 20:
        regime = "until_20"
        milestone = add_years(birth, 20)
    elif age_issue < 45:
        regime = "until_45"
        milestone = add_years(birth, 45)
    else:
        return ValidityResult(
            True,
            "indefinite_from_45",
            birth=birth.isoformat(),
            issue=issue.isoformat(),
            age_at_issue=age_issue,
            regime="indefinite",
            expires_on=None,
        )

    # действует до milestone включительно? По норме — до достижения возраста,
    # затем ещё grace_days после дня достижения.
    # Последний день действия = milestone + grace_days.
    expires = milestone + timedelta(days=grace_days)
    if as_of <= expires:
        return ValidityResult(
            True,
            "within_term_or_grace",
            birth=birth.isoformat(),
            issue=issue.isoformat(),
            age_at_issue=age_issue,
            regime=regime,
            expires_on=expires.isoformat(),
        )
    return ValidityResult(
        False,
        "expired_after_grace",
        birth=birth.isoformat(),
        issue=issue.isoformat(),
        age_at_issue=age_issue,
        regime=regime,
        expires_on=expires.isoformat(),
    )


def self_checks() -> list[str]:
    """Проверки на синтетических примерах."""
    lines = []
    cases = [
        # выдан до 20, 20 лет ровно в as_of → ещё в льготных 90 днях → valid
        ("01.10.2006", "01.10.2024", True, "20th birthday == as_of"),
        # выдан до 20, 20 лет были 91 день назад → invalid
        ("02.07.2006", "01.08.2024", False, "20th + 91 days before as_of"),
        # выдан до 20, 20 лет были ровно 90 дней назад → valid (последний день)
        ("03.07.2006", "01.08.2024", True, "20th + 90 days == as_of"),
        # выдан в 45 лет → бессрочно
        ("01.01.1980", "01.01.2025", True, "issued at 45"),
        # выдан в 44, 45 лет давно прошли → invalid
        ("01.01.1980", "01.01.2023", False, "issued at 43, 45+grace passed"),
        # дата выдачи в будущем относительно as_of
        ("01.01.2000", "02.10.2026", False, "issue after as_of"),
        # выдача раньше рождения
        ("01.01.2000", "01.01.1999", False, "issue before birth"),
    ]
    for birth, issue, expect, title in cases:
        got = check_validity(birth, issue)
        ok = got.valid == expect
        lines.append(
            f"{'OK' if ok else 'FAIL'} {title}: valid={got.valid} reason={got.reason} expires={got.expires_on}"
        )
        if not ok:
            lines.append(f"  expected valid={expect}, birth={birth}, issue={issue}")
    return lines


if __name__ == "__main__":
    for line in self_checks():
        print(line)
    print(asdict(check_validity("10.06.2010", "11.09.2025")))
