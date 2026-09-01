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
    uri_hash: str
    protocol: str
    address: str
    port: int
    is_healthy: bool
    is_enabled: bool = True
    is_manual: bool = False
    remark: str | None = None
    note: str | None = None
    latency_ms: int | None = None
    last_checked_at: dt | None = None

    model_config = ConfigDict(from_attributes=True)


class FreeConfigPage(BaseModel):
    """One page of the pool, for the admin UI's table."""

    items: list[FreeConfigResponse] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = 50
    protocols: list[str] = Field(default_factory=list)


class FreeConfigOverrideUpdate(BaseModel):
    """The admin's decision about one config. Omitted fields are left alone."""

    is_enabled: bool | None = None
    remark: str | None = Field(default=None, max_length=256)
    note: str | None = Field(default=None, max_length=512)


class BulkOverrideUpdate(BaseModel):
    uri_hashes: list[str] = Field(default_factory=list, max_length=1000)
    is_enabled: bool


class ManualConfigCreate(BaseModel):
    uri: str = Field(max_length=8192, description="a full proxy URI, e.g. vless://...")
    remark: str | None = Field(default=None, max_length=256)

    @field_validator("uri", mode="after")
    @classmethod
    def validate_uri(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith(SUPPORTED_SCHEMES):
            raise ValueError(f"uri must start with one of: {', '.join(SUPPORTED_SCHEMES)}")
        return value


class FreeConfigSettingsUpdate(BaseModel):
    """Settings the panel may change. Null puts a field back on its .env default."""

    mode: str | None = None
    refresh_interval: int | None = Field(default=None, ge=60)
    fetch_timeout: int | None = Field(default=None, ge=1, le=300)
    health_check: bool | None = None
    tcp_timeout: int | None = Field(default=None, ge=1, le=60)
    max_concurrency: int | None = Field(default=None, ge=1, le=1000)
    max_configs: int | None = Field(default=None, ge=0)
    max_per_endpoint: int | None = Field(default=None, ge=0)
    max_per_subscription: int | None = Field(default=None, ge=0)
    remark_prefix: str | None = Field(default=None, max_length=64)
    disabled: bool | None = None

    @field_validator("mode", mode="after")
    @classmethod
    def validate_mode(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if value not in ("all", "groups"):
            raise ValueError('mode must be either "all" or "groups"')
        return value


class FreeConfigSettingsResponse(BaseModel):
    effective: dict
    stored: dict
    defaults: dict
    env_enabled: bool = Field(
        description="FREE_CONFIGS_ENABLED in .env. When false the panel cannot switch the feature on."
    )


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
    pool_disabled: int = 0
    pool_manual: int = 0
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


class ConfigFieldOption(BaseModel):
    key: str
    label: str
    value: str = ""
    options: list[str] | None = None
    secret: bool = False


class ConfigFieldsResponse(BaseModel):
    """One config broken into the parts a client would show you."""

    protocol: str
    alias: str = ""
    address: str
    port: int
    uri: str
    fields: list[ConfigFieldOption] = Field(default_factory=list)
    suggested: list[ConfigFieldOption] = Field(
        default_factory=list, description="parameters this protocol commonly has but this config does not"
    )


class ConfigFieldsUpdate(BaseModel):
    """The edited config. `params` replaces the parameter set outright."""

    alias: str = Field(default="", max_length=256)
    address: str = Field(max_length=256)
    port: int = Field(ge=1, le=65535)
    params: dict[str, str] = Field(default_factory=dict)


class GroupSummary(BaseModel):
    """A panel group and what it has been given."""

    id: int
    name: str
    receives_free_configs: bool = False
    assigned_count: int = 0
    gets_whole_pool: bool = False


class GroupAssignmentUpdate(BaseModel):
    """Replace one group's config list. An empty list means the whole pool."""

    uri_hashes: list[str] = Field(default_factory=list, max_length=5000)


class GroupAssignmentPatch(BaseModel):
    """Add or remove configs without replacing the group's whole list."""

    uri_hashes: list[str] = Field(default_factory=list, max_length=5000)
    action: str = Field(default="add", pattern="^(add|remove)$")


class GroupAccessOne(BaseModel):
    enabled: bool


class GroupFreeConfigState(BaseModel):
    """Everything the panel's own group dialog needs for one group."""

    group_id: int | None = None
    enabled: bool = False
    uri_hashes: list[str] = Field(default_factory=list)
    gets_whole_pool: bool = True
