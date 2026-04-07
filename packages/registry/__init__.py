"""Multi-app Registry — manage N codebases from a single ForgeChain instance.

Each project has its own knowledge base and skills directory.
Patterns that appear in 2+ projects are auto-promoted to a shared KB
available to all future projects.

Structure on disk:
  {FORGECHAIN_PROJECTS_PATH}/
    {project_id}/
      knowledge_base/   ChromaDB for this project
      skills/           Project-specific skills files
    shared/
      knowledge_base/   Auto-promoted cross-project patterns
      skills/           Universal conventions
"""

from __future__ import annotations

from .project import Project, ProjectRegistry
from .promoter import Promoter

__all__ = ["Project", "ProjectRegistry", "Promoter"]
