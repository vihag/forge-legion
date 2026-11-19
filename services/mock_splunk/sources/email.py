"""Email security evidence."""

from pydantic import BaseModel, ConfigDict, Field

from services.mock_splunk.sources.base import SourceSpec


class EmailRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1)


SOURCE = SourceSpec(
    name="email",
    id_prefix="EV-EMAIL-",
    Record=EmailRecord,
    fixture="email.json",
)