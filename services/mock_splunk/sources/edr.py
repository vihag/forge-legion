"""Endpoint evidence: logons and process starts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from services.mock_splunk.sources.base import SourceSpec


class EdrRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1)
    event: Literal["logon", "process"]
    logon_type: int | None = Field(default=None, ge=0)
    user: str | None = None
    src_ip: str | None = None
    src_host: str | None = None
    image: str | None = None
    cmdline: str | None = None
    parent: str | None = None


SOURCE = SourceSpec(
    name="edr",
    id_prefix="EV-EDR-",
    Record=EdrRecord,
    fixture="edr.json",
)
