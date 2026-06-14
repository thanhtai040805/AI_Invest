from typing import Protocol, Any, List, Optional, Tuple

class StoragePort(Protocol):
    def execute(self, query: str, params: Optional[Tuple] = None) -> None:
        """Execute a single query without returning results."""
        ...

    def execute_values(self, query: str, values: List[Tuple], page_size: int = 100) -> None:
        """Bulk insert values."""
        ...

    def fetch_all(self, query: str, params: Optional[Tuple] = None) -> List[Any]:
        """Fetch all rows."""
        ...
