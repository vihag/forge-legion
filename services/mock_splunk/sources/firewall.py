"""Firewall allow/deny evidence."""

from pydantic import BaseModel, ConfigDict, Field

from services.mock_splunk.sources.base import SourceSpec


class FirewallRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1)


SOURCE = SourceSpec(
    name="firewall",
    id_prefix="EV-FW-",
    Record=FirewallRecord,
    fixture="firewall.json",
)