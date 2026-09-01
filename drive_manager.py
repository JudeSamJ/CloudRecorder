#!/usr/bin/env python3
"""CLI for managing the Content Creation project structure in Google Drive.

Usage:
    python drive_manager.py create-project "YouTube_003"
    python drive_manager.py list-projects
"""

import argparse
import sys

from pipeline.errors import MissingChunksError, PipelineError
from pipeline.project_manager import ProjectManager
from pipeline.reconstruction import SessionReconstructor


def cmd_create_project(args: argparse.Namespace) -> int:
    manager = ProjectManager()
    folder_id = manager.create_project(args.name)
    print(f"Created project '{args.name}' (folder id: {folder_id})")
    print(f"https://drive.google.com/drive/folders/{folder_id}")
    return 0


def cmd_list_projects(args: argparse.Namespace) -> int:
    manager = ProjectManager()
    projects = manager.list_projects()
    if not projects:
        print("No projects found.")
        return 0
    for name in projects:
        print(name)
    return 0


def cmd_reconstruct(args: argparse.Namespace) -> int:
    reconstructor = SessionReconstructor(on_progress=lambda msg: print(msg))
    try:
        result = reconstructor.reconstruct(args.session_id)
    except MissingChunksError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if exc.missing:
            print(f"  Missing chunk index(es): {exc.missing}", file=sys.stderr)
        if exc.duplicates:
            print(f"  Duplicate chunk index(es): {exc.duplicates}", file=sys.stderr)
        print("A reconstruction report with these details was uploaded to the project's Original/ folder.", file=sys.stderr)
        return 1

    print("\nReconstruction succeeded.")
    print(f"  Master file: {result.master_name}")
    print(f"  Duration: {result.duration_seconds:.2f}s")
    print(f"  Size: {result.size_bytes / (1024 * 1024):.1f} MB")
    print(f"  Chunks reassembled: {result.chunk_count}")
    print(f"  Drive file id: {result.master_file_id}")
    print("  Source chunks have been deleted from Drive.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="drive_manager.py",
        description="Manage Content Creation project folders in Google Drive.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser(
        "create-project", help="Create a new project's folder set in Drive"
    )
    create_parser.add_argument("name", help="Project name, e.g. YouTube_003")
    create_parser.set_defaults(func=cmd_create_project)

    list_parser = subparsers.add_parser(
        "list-projects", help="List existing projects in Drive"
    )
    list_parser.set_defaults(func=cmd_list_projects)

    reconstruct_parser = subparsers.add_parser(
        "reconstruct", help="Verify and reassemble a recording session's chunks into a master video"
    )
    reconstruct_parser.add_argument("session_id", help="Session ID tagged on the phone-uploaded chunks")
    reconstruct_parser.set_defaults(func=cmd_reconstruct)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except PipelineError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
