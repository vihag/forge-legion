"""DNS query evidence."""

from pydantic import BaseModel, ConfigDict, Field

from services.mock_splunk.sources.base import SourceSpec


class DnsRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)


SOURCE = SourceSpec(
    name="dns",
    id_prefix="EV-DNS-",
    Record=DnsRecord,
    fixture="dns.json",
)