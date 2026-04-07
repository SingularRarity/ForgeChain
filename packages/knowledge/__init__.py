"""ForgeChain knowledge base — RAG pipeline for local Ollama enrichment.

Workflow:
  1. Ingest: parse skills.md / documentation URLs / articles into chunks
  2. Embed:  convert chunks to vectors via nomic-embed-text (Ollama, $0)
  3. Store:  persist in ChromaDB, one collection per agent role
  4. Retrieve: at inference time, query top-k relevant chunks for a task
  5. Inject:  prepend retrieved chunks into DSPy prompt as retrieved_knowledge
"""

from .store import KnowledgeStore
from .retriever import Retriever
from .ingester import Ingester

__all__ = ["KnowledgeStore", "Retriever", "Ingester"]
