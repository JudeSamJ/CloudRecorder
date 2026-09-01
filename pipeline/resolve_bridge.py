"""DaVinci Resolve scripting bridge — Phase 7.

Confirmed against Blackmagic's published scripting documentation (not against a
live Resolve install — this sandbox has no Windows machine or Resolve on it, so
the honesty boundary below matters):

CONFIRMED, used directly:
  - The scripting API is available in the free version (not Studio-gated) for
    project creation, Media Pool import, and timeline creation — has been true
    since Resolve 16/17.
  - `MediaPoolItem.LinkProxyMedia(path)` is documented and returns a boolean, so
    we use it explicitly rather than only trusting Phase 5's filename-based
    auto-relink convention — that gives us a real success/failure signal to
    report, instead of assuming the auto-relink "probably worked."
  - One REQUIRED one-time manual setting this code cannot set for you: Resolve's
    Preferences -> General -> "External scripting using" must be "Local" (default
    is often "None"). No external Python process can connect at all until you set
    this once — see README.

NOT independently verified, handled defensively rather than assumed:
  - Whether a "prefer proxies" playback-mode key is settable via
    `SetSetting()`/`GetSetting()` at all in your installed version. Blackmagic
    doesn't publish an exhaustive settings-key list. `probe()` empirically dumps
    `GetSetting("")` (a documented way to retrieve all current settings as a
    dict, though not guaranteed present in every version) and searches it for
    "proxy" in the key name, rather than guessing a hardcoded key name and
    silently claiming success either way. If the probe can't find a workable key,
    `set_prefer_proxies()` returns False with a manual-step instruction instead
    of pretending it worked.
  - Exact startup behavior connecting to a freshly-launched (not already running)
    Resolve, including whether its own first-run Project Manager dialog needs a
    manual click before scripting can proceed on every machine/version.

Run `python drive_manager.py resolve-probe` once against your real Resolve
install to fill in the "not independently verified" gaps above with your actual
environment's answers before relying on the proxy-preference automation.
"""

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.drive_desktop import find_local_drive_root
from pipeline.errors import (
    LocalSyncNotFoundError,
    ResolveConnectionError,
    ResolveNotAvailableError,
)
from pipeline.state_store import SessionRecord

DEFAULT_RESOLVE_EXE = r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe"
CONNECT_TIMEOUT_SECONDS = 90
CONNECT_POLL_INTERVAL_SECONDS = 2


@dataclass
class ResolveChecklist:
    """One row per Phase 7 step, so callers (CLI, tray dashboard) can render an
    honest checklist instead of a single pass/fail — this is the whole point of
    "don't claim automation that didn't happen."""

    steps: list[tuple[str, bool, str]] = field(default_factory=list)

    def add(self, step: str, ok: bool, detail: str) -> None:
        self.steps.append((step, ok, detail))

    @property
    def all_ok(self) -> bool:
        return all(ok for _, ok, _ in self.steps)

    def render(self) -> str:
        lines = []
        for step, ok, detail in self.steps:
            mark = "[x]" if ok else "[ ]"
            lines.append(f"{mark} {step}{' — ' + detail if detail else ''}")
        return "\n".join(lines)


def _import_resolve_module():
    try:
        import DaVinciResolveScript as bmd  # type: ignore
    except ImportError as exc:
        raise ResolveNotAvailableError(
            "Could not import DaVinciResolveScript. This requires three "
            "environment variables to be set before Python starts (see README):\n"
            r'  RESOLVE_SCRIPT_API=%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting'
            "\n"
            r'  RESOLVE_SCRIPT_LIB=C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll'
            "\n"
            r"  PYTHONPATH must include %RESOLVE_SCRIPT_API%\Modules"
            "\n"
            "Also confirm Resolve's Preferences -> General -> 'External scripting "
            "using' is set to 'Local', not 'None' — without that, connection fails "
            "even with the environment variables correct."
        ) from exc
    return bmd


def connect(launch_if_needed: bool = True):
    """Returns a connected Resolve scripting object, launching Resolve.exe if it
    isn't already running and launch_if_needed is True. Raises ResolveConnectionError
    if it never becomes reachable within the timeout."""
    bmd = _import_resolve_module()

    resolve = bmd.scriptapp("Resolve")
    if resolve is not None:
        return resolve

    if not launch_if_needed:
        raise ResolveConnectionError(
            "Resolve does not appear to be running and launch_if_needed was False."
        )

    exe_path = os.environ.get("CLOUDRECORDER_RESOLVE_EXE", DEFAULT_RESOLVE_EXE)
    if not Path(exe_path).is_file():
        raise ResolveConnectionError(
            f"Resolve isn't running and its executable wasn't found at {exe_path}. "
            "Launch DaVinci Resolve yourself, or set CLOUDRECORDER_RESOLVE_EXE to "
            "your actual install path, then retry."
        )

    try:
        subprocess.Popen([exe_path])
    except OSError as exc:
        raise ResolveConnectionError(f"Failed to launch Resolve at {exe_path}: {exc}") from exc

    deadline = time.monotonic() + CONNECT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(CONNECT_POLL_INTERVAL_SECONDS)
        resolve = bmd.scriptapp("Resolve")
        if resolve is not None:
            return resolve

    raise ResolveConnectionError(
        f"Launched Resolve but couldn't establish a scripting connection within "
        f"{CONNECT_TIMEOUT_SECONDS}s. Resolve's own startup screen (e.g. its "
        "Project Manager) may need a manual click the very first time it opens on "
        "this machine — try again once Resolve is fully up, or check "
        "Preferences -> General -> 'External scripting using' is 'Local'."
    )


def probe() -> str:
    """Diagnostic report of what THIS Resolve install's scripting API actually
    supports — run once via `python drive_manager.py resolve-probe` before relying
    on the proxy-preference automation. Prints rather than raises for most checks,
    since the point is to report reality, not to fail fast."""
    lines = ["DaVinci Resolve scripting capability probe", "=" * 44]

    try:
        resolve = connect(launch_if_needed=True)
    except (ResolveNotAvailableError, ResolveConnectionError) as exc:
        lines.append(f"FAILED to connect: {exc}")
        return "\n".join(lines)

    lines.append(f"Connected. Product: {resolve.GetProductName()}, Version: {resolve.GetVersion()}")

    project_manager = resolve.GetProjectManager()
    if project_manager is None:
        lines.append("GetProjectManager() returned None — cannot proceed further.")
        return "\n".join(lines)
    lines.append("ProjectManager: OK")

    probe_project_name = "_cloudrecorder_probe_temp"
    project = project_manager.CreateProject(probe_project_name)
    if project is None:
        project = project_manager.LoadProject(probe_project_name)
    if project is None:
        lines.append("Could not create or load a temporary probe project — stopping here.")
        return "\n".join(lines)
    lines.append(f"Created temporary probe project '{probe_project_name}' (will be left for you to delete).")

    lines.append("")
    lines.append("Media Pool:")
    media_pool = project.GetMediaPool()
    lines.append(f"  GetMediaPool(): {'OK' if media_pool else 'MISSING'}")
    lines.append(f"  ImportMedia present: {hasattr(media_pool, 'ImportMedia')}")
    lines.append(f"  CreateEmptyTimeline present: {hasattr(media_pool, 'CreateEmptyTimeline')}")
    lines.append(f"  AppendToTimeline present: {hasattr(media_pool, 'AppendToTimeline')}")

    lines.append("")
    lines.append("Proxy-preference setting discovery:")
    try:
        all_settings = project.GetSetting("")
    except Exception as exc:  # noqa: BLE001 - reporting, not enforcing
        all_settings = None
        lines.append(f"  GetSetting(\"\") raised: {exc}")

    if isinstance(all_settings, dict) and all_settings:
        proxy_keys = {k: v for k, v in all_settings.items() if "proxy" in k.lower()}
        if proxy_keys:
            lines.append(f"  Found {len(proxy_keys)} setting key(s) containing 'proxy':")
            for key, value in proxy_keys.items():
                lines.append(f"    {key} = {value!r}")
            lines.append("  ^ candidates for set_prefer_proxies() — verify manually which one is the playback preference.")
        else:
            lines.append("  GetSetting(\"\") returned settings, but none had 'proxy' in the key name.")
            lines.append("  -> 'Prefer Proxies' is likely NOT scriptable on this version; use the manual step in the README.")
    else:
        lines.append("  GetSetting(\"\") did not return a usable settings dict on this version.")
        lines.append("  -> Cannot auto-discover a proxy-preference key here; treat it as a manual step.")

    lines.append("")
    lines.append("MediaPoolItem.LinkProxyMedia availability: checked at import time per-clip (see open_in_resolve()).")

    lines.append("")
    lines.append(f"Clean-up: delete the '{probe_project_name}' project from Resolve's Project Manager when done.")
    return "\n".join(lines)


def resolve_project_name(session: SessionRecord) -> str:
    return f"{session.project_name} - {session.session_id}"


def _local_paths_for_session(session: SessionRecord) -> tuple[Path, Path]:
    """Computes the expected local Drive-for-desktop paths for a session's master
    and proxy from the same fixed naming convention Phases 4/5 already use
    (<sessionId>_master.mp4 / .mov), rather than requiring an extra Drive API call
    just to learn a filename."""
    local_root = find_local_drive_root()
    if local_root is None:
        raise LocalSyncNotFoundError(
            "Could not find your local Google Drive for desktop sync path. Check "
            "Drive for desktop's own settings for where it syncs to, or set the "
            "CLOUDRECORDER_DRIVE_LOCAL_PATH environment variable to override "
            "auto-detection."
        )
    project_dir = local_root / "Content Creation" / "Projects" / session.project_name
    original_path = project_dir / "Original" / f"{session.session_id}_master.mp4"
    proxy_path = project_dir / "Proxy" / f"{session.session_id}_master.mov"
    return original_path, proxy_path


def open_in_resolve(session: SessionRecord) -> ResolveChecklist:
    """The Phase 7 entry point: create-or-open this session's project, import the
    original, link its proxy, best-effort set 'prefer proxies', and populate a
    timeline. Every step is independently checked and recorded in the returned
    checklist — a step that can't be confirmed is reported as failed with a
    specific manual instruction, never silently skipped.

    Works entirely from local filesystem paths (the Drive-for-desktop-synced
    Original/Proxy folders) and the Resolve scripting connection — no Drive API
    calls needed here, since Phases 4-6 already established the session's local
    presence by the time it reaches READY."""
    checklist = ResolveChecklist()

    try:
        original_path, proxy_path = _local_paths_for_session(session)
    except LocalSyncNotFoundError as exc:
        checklist.add("Locate local synced media", False, str(exc))
        return checklist

    # wait_for_local_sync (used elsewhere for the proxy) needs an exact expected
    # byte size, which we don't have for the master here — a plain existence poll
    # is enough since we're only checking whether Drive for desktop has synced the
    # file down yet, not validating its content.
    original_synced = original_path.is_file()
    if not original_synced:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not original_path.is_file():
            time.sleep(2)
        original_synced = original_path.is_file()
    checklist.add(
        "Original media synced locally",
        original_synced,
        str(original_path) if original_synced else f"Not found at {original_path} — wait for Drive for desktop to sync, then retry.",
    )
    if not original_synced:
        return checklist

    proxy_synced = proxy_path.is_file()
    if not proxy_synced:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not proxy_path.is_file():
            time.sleep(2)
        proxy_synced = proxy_path.is_file()
    checklist.add(
        "Proxy media synced locally",
        proxy_synced,
        str(proxy_path) if proxy_synced else f"Not found at {proxy_path} — wait for Drive for desktop to sync, then retry.",
    )

    try:
        resolve = connect(launch_if_needed=True)
    except (ResolveNotAvailableError, ResolveConnectionError) as exc:
        checklist.add("Connect to DaVinci Resolve", False, str(exc))
        return checklist
    checklist.add("Connect to DaVinci Resolve", True, "")

    project_manager = resolve.GetProjectManager()
    project_name = resolve_project_name(session)
    project = project_manager.CreateProject(project_name)
    reused_existing = False
    if project is None:
        # CreateProject returns None if a project with this name already exists —
        # this is the expected path on a second "Open in Resolve" click for the
        # same session, per the one-project-per-session structure.
        project = project_manager.LoadProject(project_name)
        reused_existing = project is not None
    if project is None:
        checklist.add("Create or open Resolve project", False, f"Could not create or load project '{project_name}'.")
        return checklist
    checklist.add(
        "Create or open Resolve project",
        True,
        f"{'Opened existing' if reused_existing else 'Created new'} project '{project_name}'",
    )

    media_pool = project.GetMediaPool()
    if media_pool is None:
        checklist.add("Access Media Pool", False, "GetMediaPool() returned None.")
        return checklist

    imported_items = []
    if original_synced:
        imported_items = media_pool.ImportMedia([str(original_path)]) or []
    checklist.add(
        "Import original media into Media Pool",
        bool(imported_items),
        original_path.name if imported_items else "ImportMedia returned no items — import it manually via Media Pool.",
    )

    if imported_items and proxy_synced:
        clip = imported_items[0]
        linked = False
        try:
            linked = bool(clip.LinkProxyMedia(str(proxy_path)))
        except Exception as exc:  # noqa: BLE001 - report, don't crash the whole run over this
            checklist.add("Link proxy media", False, f"LinkProxyMedia raised: {exc}")
        else:
            checklist.add(
                "Link proxy media",
                linked,
                proxy_path.name if linked
                else "LinkProxyMedia returned False — right-click the clip in Resolve and use 'Link Proxy Media' manually.",
            )
    else:
        checklist.add("Link proxy media", False, "Skipped — original wasn't imported or proxy isn't synced yet.")

    prefer_proxies_ok, prefer_proxies_detail = set_prefer_proxies(project)
    checklist.add("Set 'Prefer Proxies' for this project", prefer_proxies_ok, prefer_proxies_detail)

    if imported_items:
        timeline_name = f"{session.session_id} - Timeline"
        timeline = media_pool.CreateEmptyTimeline(timeline_name)
        appended = False
        if timeline is not None:
            appended = bool(media_pool.AppendToTimeline(imported_items))
        checklist.add(
            "Create timeline with clip(s)",
            bool(timeline is not None and appended),
            timeline_name if (timeline is not None and appended) else "Could not create/populate a timeline — add the clip to a timeline manually.",
        )
    else:
        checklist.add("Create timeline with clip(s)", False, "Skipped — nothing was imported.")

    return checklist


# Key names occasionally reported in community Resolve-scripting discussions for
# the playback proxy-mode preference. NOT confirmed against Blackmagic's own
# documentation — probe() exists specifically because this list may be wrong or
# incomplete for your version. Tried in order; the first one that visibly changes
# GetSetting()'s returned value is trusted, everything else is treated as failure.
_PROXY_MODE_KEY_CANDIDATES = ["perfProxyMediaMode", "proxyMediaMode"]
_PREFER_PROXIES_VALUE_CANDIDATES = ["1", "PreferProxy", "Prefer Proxies"]


def set_prefer_proxies(project) -> tuple[bool, str]:
    """Best-effort attempt to set the project to prefer proxy media, verified by
    reading the setting back rather than trusting SetSetting's return value alone
    (some Resolve API versions return True even when the key didn't apply). Falls
    back to an explicit manual instruction on failure rather than claiming success."""
    for key in _PROXY_MODE_KEY_CANDIDATES:
        try:
            before = project.GetSetting(key)
        except Exception:  # noqa: BLE001
            continue
        if before is None or before == "":
            continue  # key doesn't exist on this version, try the next candidate
        for value in _PREFER_PROXIES_VALUE_CANDIDATES:
            try:
                project.SetSetting(key, value)
                after = project.GetSetting(key)
            except Exception:  # noqa: BLE001
                continue
            if after == value and after != before:
                return True, f"Set '{key}' = '{value}'"
    return False, (
        "Could not confirm a scriptable proxy-preference setting on this Resolve "
        "version. Set it manually: Playback menu -> Proxy Handling -> Prefer "
        "Proxies (or the proxy icon in the timeline toolbar)."
    )
