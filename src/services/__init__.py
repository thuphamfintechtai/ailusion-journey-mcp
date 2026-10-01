"""Services package — nơi gọi upstream (BE, LLM, DB).

Tool chỉ orchestrate; mọi I/O nằm ở đây để test được độc lập.
"""

from .http_client import backend_client, close_http_clients, llm_client
from .serpapi_client import SerpApiError, serpapi_client

__all__ = [
    "SerpApiError",
    "backend_client",
    "close_http_clients",
    "llm_client",
    "serpapi_client",
]
