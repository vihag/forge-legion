"""Web-proxy evidence: client, method, URL, and byte counts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from services.mock_splunk.sources.base import SourceSpec


class ProxyRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client: str = Field(min_length=1)
    method: Literal["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"]
    url: str = Field(min_length=1)
    category: str | None = None
    first_seen_domain: bool | None = None
    bytes_in: int | None = Field(default=None, ge=0)
    bytes_out: int | None = Field(default=None, ge=0)


SOURCE = SourceSpec(
    name="proxy",
    id_prefix="EV-PROXY-",
    Record=ProxyRecord,
    fixture="proxy.json",
)
