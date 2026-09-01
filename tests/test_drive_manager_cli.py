"""CLI wiring tests — argparse structure and the open-resolve guard logic, none
of which touch the network, ffmpeg, or Resolve."""

import pytest

import drive_manager
from pipeline import state_store as store
from pipeline.errors import SessionNotReadyError


def test_parser_requires_a_command():
    parser = drive_manager.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_create_project_parses_name():
    parser = drive_manager.build_parser()
    args = parser.parse_args(["create-project", "YouTube_003"])
    assert args.command == "create-project"
    assert args.name == "YouTube_003"
    assert args.func is drive_manager.cmd_create_project


def test_list_projects_parses():
    parser = drive_manager.build_parser()
    args = parser.parse_args(["list-projects"])
    assert args.func is drive_manager.cmd_list_projects


def test_reconstruct_requires_session_id():
    parser = drive_manager.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["reconstruct"])
    args = parser.parse_args(["reconstruct", "SID123"])
    assert args.session_id == "SID123"
    assert args.func is drive_manager.cmd_reconstruct


def test_generate_proxy_session_id_optional_with_watch():
    parser = drive_manager.build_parser()
    args = parser.parse_args(["generate-proxy", "--watch"])
    assert args.session_id is None
    assert args.watch is True
    assert args.interval == 60


def test_generate_proxy_interval_override():
    parser = drive_manager.build_parser()
    args = parser.parse_args(["generate-proxy", "--watch", "--interval", "30"])
    assert args.interval == 30


def test_open_resolve_requires_session_id():
    parser = drive_manager.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["open-resolve"])
    args = parser.parse_args(["open-resolve", "SID123"])
    assert args.session_id == "SID123"
    assert args.func is drive_manager.cmd_open_resolve


def test_resolve_probe_parses_with_no_args():
    parser = drive_manager.build_parser()
    args = parser.parse_args(["resolve-probe"])
    assert args.func is drive_manager.cmd_resolve_probe


def test_cmd_open_resolve_raises_when_session_unknown(state_store):
    class Args:
        session_id = "does-not-exist"

    with pytest.raises(SessionNotReadyError, match="isn't known"):
        drive_manager.cmd_open_resolve(Args())


def test_cmd_open_resolve_raises_when_not_ready(state_store):
    store.add_new_session("s1", "P", 1, 1, "m1")  # defaults to AWAITING_RECONSTRUCTION

    class Args:
        session_id = "s1"

    with pytest.raises(SessionNotReadyError, match="not READY"):
        drive_manager.cmd_open_resolve(Args())


def test_main_reports_pipeline_error_and_returns_1(monkeypatch, capsys):
    def boom(args):
        from pipeline.errors import NetworkError
        raise NetworkError("Drive unreachable")

    monkeypatch.setattr(drive_manager.sys, "argv", ["drive_manager.py", "list-projects"])
    monkeypatch.setattr(drive_manager, "cmd_list_projects", boom)
    exit_code = drive_manager.main()
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Drive unreachable" in captured.err
