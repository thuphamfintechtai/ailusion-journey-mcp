"""Services package — nơi gọi upstream (BE, LLM, DB).

Tool chỉ orchestrate; mọi I/O nằm ở đây để test được độc lập.
"""

from .http_client import backend_client, close_http_clients, llm_client
from .serpapi_client import SerpApiError, serpapi_client
from .tavily_client import TavilyError, tavily_client
from .vietmap_client import VietmapError, vietmap_client

__all__ = [
    "SerpApiError",
    "TavilyError",
    "VietmapError",
    "backend_client",
    "close_http_clients",
    "llm_client",
    "serpapi_client",
    "tavily_client",
    "vietmap_client",
]
