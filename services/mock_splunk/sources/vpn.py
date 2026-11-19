"""VPN session evidence."""

from pydantic import BaseModel, ConfigDict, Field

from services.mock_splunk.sources.base import SourceSpec


class VpnRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: str = Field(min_length=1)


SOURCE = SourceSpec(
    name="vpn",
    id_prefix="EV-VPN-",
    Record=VpnRecord,
    fixture="vpn.json",
)