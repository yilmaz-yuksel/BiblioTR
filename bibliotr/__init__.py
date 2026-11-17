"""TR Dizin API istemcisi ve bibliyometrik analiz araçları."""

from .client import TRDizinClient, TRDizinError
from .normalize import Author, Publication, normalize_publication
from .storage import Store

__all__ = ["TRDizinClient", "TRDizinError", "Author", "Publication", "normalize_publication", "Store"]
__version__ = "1.1.0"
