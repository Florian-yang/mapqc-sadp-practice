"""Pure data types; no window or file access."""
from dataclasses import dataclass, field
from pathlib import Path
import os
from typing import Any, Literal


def document_key(path: Path) -> str:
    return str(path.resolve()).casefold() if os.name == 'nt' else str(path.resolve())


@dataclass(frozen=True)
class Location:
    kind: Literal['vector', 'text', 'raster']
    x: float | None = None
    y: float | None = None
    line: int | None = None
    row: int | None = None
    column: int | None = None

    def __post_init__(self):
        import math
        if self.kind == 'vector':
            valid = all(type(v) in (int, float) and math.isfinite(v) for v in (self.x, self.y)) and all(v is None for v in (self.line, self.row, self.column))
        elif self.kind == 'text':
            valid = type(self.line) is int and self.line >= 1 and all(v is None for v in (self.x, self.y, self.row, self.column))
        elif self.kind == 'raster':
            valid = all(type(v) is int and v >= 0 for v in (self.row, self.column)) and all(v is None for v in (self.x, self.y, self.line))
        else:
            valid = False
        if not valid:
            raise ValueError('位置类型或坐标无效')


@dataclass
class MapDocument:
    document_key: str
    path: Path
    kind: Literal['vector', 'text']
    features: list[dict[str, Any]] = field(default_factory=list)
    text: str = ''

    @property
    def lines(self) -> list[str]:
        return self.text.splitlines()


@dataclass(frozen=True)
class ErrorRecord:
    document_key: str
    number: int
    location: Location
    error_type: str
    description: str
