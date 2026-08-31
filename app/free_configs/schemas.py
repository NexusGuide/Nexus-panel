"""API schemas for the free-configs feature."""

from datetime import datetime as dt

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.free_configs.parser import SUPPORTED_SCHEMES


class FreeConfigSourceCreate(BaseModel):
    url: str = Field(max_length=512, description="URL of a public list of proxy URIs")
    remark: str = Field(default="", max_length=256)
    is_base64: bool = Field(default=False, description="whole response body is base64 encoded")

    @field_validator("url", mode="after")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return value


class FreeConfigSourceModify(BaseModel):
    remark: str | None = Field(default=None, max_length=256)
    is_base64: bool | None = None
    is_enabled: bool | None = None


class FreeConfigSourceResponse(BaseModel):
    id: int
    url: str
    remark: str
    is_base64: bool
    is_enabled: bool
    last_fetch_at: dt | None = None
    last_fetch_count: int = 0
    last_error: str | None = None
    created_at: dt

    model_config = ConfigDict(from_attributes=True)


class FreeConfigResponse(BaseModel):
    id: int
    uri: str
    protocol: str
    address: str
    port: int
    is_healthy: bool
    latency_ms: int | None = None
    last_checked_at: dt | None = None

    model_config = ConfigDict(from_attributes=True)


class RefreshStats(BaseModel):
    sources: int = 0
    fetched: int = 0
    unique: int = 0
    healthy: int = 0
    duration_seconds: float = 0.0
    errors: list[dict] = Field(default_factory=list)


class FreeConfigsStatus(BaseModel):
    enabled: bool
    mode: str
    refresh_interval: int
    health_check: bool
    running: bool
    last_refresh_at: dt | None = None
    last_stats: RefreshStats | None = None
    pool_total: int = 0
    pool_healthy: int = 0
    pool_last_checked_at: dt | None = None
    supported_schemes: list[str] = Field(default_factory=lambda: list(SUPPORTED_SCHEMES))
    note: str = (
        "Health means the endpoint answered a TCP connect from this server - not that the proxy "
        "protocol works, and not that it is reachable from an end user's network. Free configs are "
        "appended only to the links / links_base64 subscription formats."
    )


class GroupAccessUpdate(BaseModel):
    group_ids: list[int] = Field(default_factory=list)


class GroupAccessResponse(BaseModel):
    group_ids: list[int] = Field(default_factory=list)
