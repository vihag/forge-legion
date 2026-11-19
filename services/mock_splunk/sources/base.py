"""Source plug-in contract. A new source does not change the API schema."""

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel


class Source(Protocol):
    """One evidence source: a name, an id prefix, a content model, and a fixture file."""

    name: str
    id_prefix: str
    Record: type[BaseModel]
    fixture: str


@dataclass
class SourceSpec:
    """Concrete registration. ``Record`` validates each fixture record's ``content``."""

    name: str
    id_prefix: str
    Record: type[BaseModel]
    fixture: str
