"""ForgeChain knowledge base CLI.

Commands:
  ingest file   — add a local skills.md / doc file
  ingest url    — scrape and add a documentation URL
  ingest dir    — recursively add a directory of markdown files
  status        — show chunk counts and sources per role
  delete        — remove a source from the knowledge base
  list-roles    — show all roles that have knowledge

Examples:
  python -m knowledge.cli ingest file --role backend_dev skills/backend_dev.md
  python -m knowledge.cli ingest url  --role backend_dev https://fastapi.tiangolo.com/tutorial/
  python -m knowledge.cli ingest dir  --role frontend_dev docs/react/
  python -m knowledge.cli status --role backend_dev
  python -m knowledge.cli delete --role backend_dev --source skills/old.md
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# Allow running as `python -m knowledge.cli` from /packages
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from knowledge.ingester import Ingester
from knowledge.store import KnowledgeStore

ALL_ROLES = [
    "frontend_dev", "backend_dev", "qa_backend",
    "db_eng", "ai_eng", "sre", "ba",
]


def cmd_ingest(args: argparse.Namespace) -> None:
    project_id = getattr(args, "project", None) or None
    ing = Ingester(role=args.role, project_id=project_id)

    if args.subcommand == "file":
        count = ing.ingest_file(args.path)
    elif args.subcommand == "url":
        count = ing.ingest_url(args.url)
    elif args.subcommand == "dir":
        count = ing.ingest_dir(args.directory)
    else:
        print(f"Unknown ingest subcommand: {args.subcommand}")
        sys.exit(1)

    scope = f"project '{project_id}'" if project_id else "global KB"
    print(f"✓ Ingested {count} chunks into role '{args.role}' ({scope})")


def cmd_scan(args: argparse.Namespace) -> None:
    """Auto-scan a local repo folder and ingest into a project KB."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from registry.scanner import RepoScanner

    print(f"Scanning {args.repo_path} → project '{args.project}' ...")
    scanner = RepoScanner(project_id=args.project)
    result = scanner.scan(args.repo_path)
    print(result.summary())
    if result.warnings:
        for w in result.warnings:
            print(f"  ⚠  {w}")


def cmd_status(args: argparse.Namespace) -> None:
    roles = [args.role] if args.role else ALL_ROLES
    for role in roles:
        store = KnowledgeStore(role)
        info = {
            "role": role,
            "chunks": store.count(),
            "sources": store.list_sources(),
        }
        print(json.dumps(info, indent=2))


def cmd_delete(args: argparse.Namespace) -> None:
    ing = Ingester(role=args.role)
    ing.delete_source(args.source)
    print(f"✓ Deleted source '{args.source}' from role '{args.role}'")


def cmd_list_roles(_args: argparse.Namespace) -> None:
    for role in ALL_ROLES:
        store = KnowledgeStore(role)
        count = store.count()
        if count > 0:
            print(f"  {role:20s}  {count:>6d} chunks")
    print()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m knowledge.cli",
        description="ForgeChain knowledge base manager",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # ingest
    ingest_p = sub.add_parser("ingest", help="Add content to the knowledge base")
    ingest_p.add_argument("--role", required=True, choices=ALL_ROLES)
    ingest_p.add_argument(
        "--project", default=None,
        help="Project ID for multi-app scoping (omit for global KB)",
    )
    ingest_sub = ingest_p.add_subparsers(dest="subcommand", required=True)

    file_p = ingest_sub.add_parser("file", help="Ingest a local file")
    file_p.add_argument("path", help="Path to .md / .txt / .rst file")

    url_p = ingest_sub.add_parser("url", help="Scrape and ingest a URL")
    url_p.add_argument("url", help="https://...")

    dir_p = ingest_sub.add_parser("dir", help="Ingest all docs in a directory")
    dir_p.add_argument("directory", help="Path to directory")

    # scan
    scan_p = sub.add_parser(
        "scan",
        help="Auto-scan a local repo folder and ingest its docs into a project KB",
    )
    scan_p.add_argument("--project", required=True, help="Target project ID")
    scan_p.add_argument("repo_path", help="Absolute path to the local codebase")

    # status
    status_p = sub.add_parser("status", help="Show knowledge base stats")
    status_p.add_argument("--role", choices=ALL_ROLES, default=None,
                          help="Role to inspect (all if omitted)")

    # delete
    del_p = sub.add_parser("delete", help="Remove a source from the knowledge base")
    del_p.add_argument("--role", required=True, choices=ALL_ROLES)
    del_p.add_argument("--source", required=True, help="Source path or URL to remove")

    # list-roles
    sub.add_parser("list-roles", help="List roles with knowledge")

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "ingest":     cmd_ingest,
        "status":     cmd_status,
        "delete":     cmd_delete,
        "list-roles": cmd_list_roles,
        "scan":       cmd_scan,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
