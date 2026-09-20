"""Services package — nơi gọi upstream (BE, LLM, DB).

Tool chỉ orchestrate; mọi I/O nằm ở đây để test được độc lập.
"""

from .http_client import backend_client, close_http_clients, llm_client

__all__ = ["backend_client", "close_http_clients", "llm_client"]
