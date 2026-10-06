"""Cấu hình cho Ailusion Journey MCP Server."""

from dotenv import load_dotenv
from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings

# Nạp .env trước khi Settings đọc env
load_dotenv(override=True)


class Settings(BaseSettings):
    """Settings đọc từ biến môi trường / file .env."""

    # --- Server ---
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")
    mcp_transport: str = Field(default="http")
    mcp_host: str = Field(default="0.0.0.0")
    mcp_port: int = Field(default=8000)

    # --- Upstream trong hệ Ailusion Journey ---
    backend_base_url: str = Field(default="http://localhost:3000")
    backend_api_key: str = Field(default="")
    llm_base_url: str = Field(default="http://localhost:8001")
    llm_api_key: str = Field(default="")

    # --- SerpApi (dữ liệu Google Hotels / Flights / Maps, TripAdvisor...) ---
    # Nhiều key cách nhau bởi dấu phẩy. Giữ dạng chuỗi thay vì list[str] vì
    # pydantic-settings đòi list phải viết dạng JSON trong .env.
    serpapi_api_keys: str = Field(default="")
    serpapi_base_url: str = Field(default="https://serpapi.com")
    serpapi_timeout: int = Field(default=60)
    # Thời gian (giây) cho key nghỉ sau lỗi, xem docstring serpapi_client.
    serpapi_rate_limit_cooldown: int = Field(default=900)
    serpapi_quota_cooldown: int = Field(default=6 * 3600)
    serpapi_invalid_key_cooldown: int = Field(default=24 * 3600)

    # --- Lịch Việt Nam (tool vn_calendar) ---
    # Các nguồn bật, cách nhau dấu phẩy; xem src/services/vn_calendar/sources.py
    calendar_sources: str = Field(default="rules,google_ics,overrides")
    calendar_ics_url: str = Field(
        default="https://calendar.google.com/calendar/ical/vi.vietnamese%23holiday%40group.v.calendar.google.com/public/basic.ics"
    )
    calendar_ics_ttl: int = Field(default=24 * 3600)
    # Bỏ trống thì dùng file đóng gói trong src/data/calendar/
    calendar_rules_file: str = Field(default="")
    calendar_overrides_dir: str = Field(default="")

    # --- HTTP client ---
    http_timeout: int = Field(default=30)
    http_max_retries: int = Field(default=2)

    # --- Mặc định nghiệp vụ ---
    default_language: str = Field(default="vi")
    default_currency: str = Field(default="VND")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"Log level must be one of: {valid_levels}")
        return v.upper()

    @property
    def serpapi_key_list(self) -> list[str]:
        """Danh sách key SerpApi đã bỏ khoảng trắng và phần tử rỗng."""
        return [k.strip() for k in self.serpapi_api_keys.split(",") if k.strip()]

    @field_validator("mcp_transport")
    @classmethod
    def validate_transport(cls, v: str) -> str:
        valid = ["http", "stdio", "sse", "streamable-http"]
        if v.lower() not in valid:
            raise ValueError(f"Transport must be one of: {valid}")
        return v.lower()

    model_config = {
        "env_file": ".env",
        "case_sensitive": False,
        "extra": "ignore",
    }


try:
    settings = Settings()
except ValidationError as e:
    missing_fields = [
        str(error["loc"][0]).upper() for error in e.errors() if error["type"] == "missing"
    ]
    if missing_fields:
        print(f"Thiếu biến môi trường bắt buộc: {missing_fields}")
        print("Khai báo các biến này trong file .env hoặc environment")
        raise ValueError(f"Thiếu biến môi trường: {missing_fields}") from e
    raise
