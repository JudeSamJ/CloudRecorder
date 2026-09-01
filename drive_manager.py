#!/usr/bin/env python3
"""CLI for managing the Content Creation project structure in Google Drive.

Usage:
    python drive_manager.py create-project "YouTube_003"
    python drive_manager.py list-projects
"""

import argparse
import sys
import time

from pipeline import resolve_bridge
from pipeline import state_store as store
from pipeline.drive_client import DriveClient
from pipeline.errors import MissingChunksError, PipelineError, SessionNotReadyError
from pipeline.project_manager import ProjectManager
from pipeline.proxy_generation import ProxyGenerator, find_sessions_needing_proxy
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


def _print_proxy_result(result) -> None:
    print("\nProxy generation succeeded.")
    print(f"  Proxy file: {result.proxy_name}")
    print(f"  Duration: {result.duration_seconds:.2f}s")
    print(f"  Resolution: {result.width}x{result.height}")
    print(f"  Size: {result.size_bytes / (1024 * 1024):.1f} MB")
    print(f"  Hardware-accelerated decode: {result.hardware_decode_used or 'no (software)'}")
    if result.local_synced_path:
        status = "confirmed synced" if result.local_sync_confirmed else "not yet confirmed (still syncing)"
        print(f"  Local path: {result.local_synced_path} ({status})")
    else:
        print("  Local path: could not be auto-detected — check Google Drive for desktop's settings "
              "for your Drive folder location; the proxy is already safely uploaded regardless.")


def cmd_generate_proxy(args: argparse.Namespace) -> int:
    if not args.watch and not args.session_id:
        print("Error: session_id is required unless --watch is given.", file=sys.stderr)
        return 2

    generator = ProxyGenerator(on_progress=lambda msg: print(msg))

    if not args.watch:
        result = generator.generate_for_session(args.session_id)
        _print_proxy_result(result)
        return 0

    print(f"Watching for new masters every {args.interval}s (Ctrl+C to stop)...")
    client = DriveClient()
    while True:
        try:
            sessions = find_sessions_needing_proxy(client)
            for session_id in sessions:
                print(f"\nFound reconstructed master with no proxy: session '{session_id}'")
                try:
                    result = generator.generate_for_session(session_id)
                    _print_proxy_result(result)
                except PipelineError as exc:
                    print(f"  Error generating proxy for '{session_id}': {exc}", file=sys.stderr)
        except PipelineError as exc:
            print(f"Error while checking for new masters: {exc}", file=sys.stderr)
        time.sleep(args.interval)


def cmd_open_resolve(args: argparse.Namespace) -> int:
    store.init_db()
    session = store.get_session(args.session_id)
    if session is None:
        raise SessionNotReadyError(
            f"Session '{args.session_id}' isn't known to the companion app's local "
            "state yet. Run the companion app (companion_app.py) and let it "
            "discover this session first, or check the session ID."
        )
    if session.stage != store.READY:
        raise SessionNotReadyError(
            f"Session '{args.session_id}' is at stage '{session.stage}', not READY "
            "yet — its proxy hasn't been validated and synced. Check the companion "
            "app's dashboard for its current status."
        )

    print(f"Opening session '{args.session_id}' (project '{session.project_name}') in Resolve...\n")
    checklist = resolve_bridge.open_in_resolve(session)
    print(checklist.render())
    return 0 if checklist.all_ok else 1


def cmd_resolve_probe(args: argparse.Namespace) -> int:
    print(resolve_bridge.probe())
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

    proxy_parser = subparsers.add_parser(
        "generate-proxy", help="Generate a DNxHR editing proxy from a session's reconstructed master"
    )
    proxy_parser.add_argument(
        "session_id", nargs="?", default=None,
        help="Session ID of a reconstructed master (omit only when using --watch)",
    )
    proxy_parser.add_argument(
        "--watch", action="store_true",
        help="Continuously watch for newly-reconstructed masters and auto-generate proxies for them",
    )
    proxy_parser.add_argument(
        "--interval", type=int, default=60,
        help="Polling interval in seconds for --watch (default: 60)",
    )
    proxy_parser.set_defaults(func=cmd_generate_proxy)

    open_resolve_parser = subparsers.add_parser(
        "open-resolve", help="Open a READY session in DaVinci Resolve (create/open project, import, link proxy, timeline)"
    )
    open_resolve_parser.add_argument("session_id", help="Session ID marked READY by the companion app")
    open_resolve_parser.set_defaults(func=cmd_open_resolve)

    resolve_probe_parser = subparsers.add_parser(
        "resolve-probe", help="Diagnose what your installed DaVinci Resolve's scripting API actually supports"
    )
    resolve_probe_parser.set_defaults(func=cmd_resolve_probe)

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
