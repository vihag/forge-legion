"""Cloud control-plane evidence."""

from pydantic import BaseModel, ConfigDict, Field

from services.mock_splunk.sources.base import SourceSpec


class CloudRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_call: str = Field(min_length=1)


SOURCE = SourceSpec(
    name="cloud",
    id_prefix="EV-CLOUD-",
    Record=CloudRecord,
    fixture="cloud.json",
)