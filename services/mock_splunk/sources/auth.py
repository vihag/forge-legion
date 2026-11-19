"""Authentication evidence: success and failure, MFA, and source country."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from services.mock_splunk.sources.base import SourceSpec


class AuthRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: Literal["success", "failure"]
    user: str = Field(min_length=1)
    src_ip: str = Field(min_length=1)
    src_country: str = Field(pattern=r"^[A-Z]{2}$")
    mfa: Literal["approved", "denied"] | None = None
    mfa_method: Literal["push"] | None = None
    reason: Literal["bad_password"] | None = None
    target: str | None = None
    first_seen_country: bool | None = None
    vpn_ip: str | None = None


SOURCE = SourceSpec(
    name="auth",
    id_prefix="EV-AUTH-",
    Record=AuthRecord,
    fixture="auth.json",
)
