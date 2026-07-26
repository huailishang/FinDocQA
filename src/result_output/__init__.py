"""Output-neutral result contracts and adapters."""
from .contracts import OutputAdapter, ResultRecord
from .json_adapter import JsonResultWriter

__all__ = ["JsonResultWriter", "OutputAdapter", "ResultRecord"]
