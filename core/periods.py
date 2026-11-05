"""Periode laporan: 1Q (tw1), 2Q (tw2), 3Q (tw3), FY (audit)."""
import re
from dataclasses import dataclass
from datetime import date

CODES = ["tw1", "tw2", "tw3", "audit"]
PREFIX = {"tw1": "1Q", "tw2": "2Q", "tw3": "3Q", "audit": "FY"}
CUM_PREFIX = {"tw1": "3M", "tw2": "6M", "tw3": "9M", "audit": "FY"}
QTR_PREFIX = {"tw1": "1Q", "tw2": "2Q", "tw3": "3Q", "audit": "4Q"}
END = {"tw1": (3, 31), "tw2": (6, 30), "tw3": (9, 30), "audit": (12, 31)}
MONTHS = {3: "tw1", 6: "tw2", 9: "tw3", 12: "audit"}


@dataclass(frozen=True, order=True)
class Period:
    year: int
    idx: int  # 0..3

    @classmethod
    def of(cls, year: int, code: str) -> "Period":
        return cls(int(year), CODES.index(code))

    @classmethod
    def parse_key(cls, key: str) -> "Period":
        y, c = key.split("-")
        return cls.of(int(y), c)

    @classmethod
    def parse_label(cls, label: str) -> "Period":
        m = re.fullmatch(r"(1Q|2Q|3Q|4Q|FY)(\d{2})", label.strip().upper())
        if not m:
            raise ValueError(label)
        code = {"1Q": "tw1", "2Q": "tw2", "3Q": "tw3", "4Q": "audit", "FY": "audit"}[m.group(1)]
        return cls.of(2000 + int(m.group(2)), code)

    @property
    def code(self) -> str:
        return CODES[self.idx]

    @property
    def key(self) -> str:
        return f"{self.year}-{self.code}"

    @property
    def yy(self) -> str:
        return str(self.year)[-2:]

    @property
    def label(self) -> str:          # nama folder/periode: 1Q26, FY25
        return f"{PREFIX[self.code]}{self.yy}"

    @property
    def cum_label(self) -> str:      # 3M26, 6M26, 9M26, FY26
        return f"{CUM_PREFIX[self.code]}{self.yy}"

    @property
    def q_label(self) -> str:        # 1Q26 ... 4Q26
        return f"{QTR_PREFIX[self.code]}{self.yy}"

    @property
    def end_date(self) -> date:
        m, d = END[self.code]
        return date(self.year, m, d)

    @property
    def quarters(self) -> int:
        return self.idx + 1

    def prev(self) -> "Period":
        return Period(self.year, self.idx - 1) if self.idx else Period(self.year - 1, 3)

    def next(self) -> "Period":
        return Period(self.year, self.idx + 1) if self.idx < 3 else Period(self.year + 1, 0)

    def same_last_year(self) -> "Period":
        return Period(self.year - 1, self.idx)


def all_periods_until(start_year: int, today: date | None = None) -> list[Period]:
    today = today or date.today()
    out, p = [], Period(start_year, 0)
    while p.end_date < today:
        out.append(p)
        p = p.next()
    return out
