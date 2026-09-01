from pathlib import Path

from pipeline import ffmpeg_tools, proxy_generation


def test_write_concat_list_format(tmp_path):
    chunks = [Path("/videos/chunk_0001.mp4"), Path("/videos/chunk_0002.mp4")]
    list_file = tmp_path / "list.txt"
    ffmpeg_tools.write_concat_list(chunks, list_file)
    content = list_file.read_text(encoding="utf-8")
    assert content == "file '/videos/chunk_0001.mp4'\nfile '/videos/chunk_0002.mp4'"


def test_write_concat_list_single_chunk(tmp_path):
    list_file = tmp_path / "list.txt"
    ffmpeg_tools.write_concat_list([Path("/a/b.mp4")], list_file)
    assert list_file.read_text(encoding="utf-8") == "file '/a/b.mp4'"


def test_report_progress_computes_percent_and_eta():
    captured = []
    block = {"out_time_us": "5000000", "speed": "2.5x"}  # 5s elapsed at 2.5x speed
    ffmpeg_tools._report_progress(block, total_duration_seconds=10.0, on_progress=lambda p, e: captured.append((p, e)))
    percent, eta = captured[0]
    assert percent == 50.0
    assert eta == 2.0  # (10 - 5) / 2.5


def test_report_progress_clamps_percent_at_100(tmp_path):
    captured = []
    block = {"out_time_us": "20000000", "speed": "1x"}  # overshoot past total duration
    ffmpeg_tools._report_progress(block, total_duration_seconds=10.0, on_progress=lambda p, e: captured.append((p, e)))
    percent, _ = captured[0]
    assert percent == 100.0


def test_report_progress_handles_missing_out_time():
    captured = []
    ffmpeg_tools._report_progress({}, total_duration_seconds=10.0, on_progress=lambda p, e: captured.append((p, e)))
    assert captured == []  # nothing reported when out_time is absent, not a crash


def test_report_progress_handles_zero_speed_no_eta():
    captured = []
    block = {"out_time_us": "1000000", "speed": "0x"}
    ffmpeg_tools._report_progress(block, total_duration_seconds=10.0, on_progress=lambda p, e: captured.append((p, e)))
    _, eta = captured[0]
    assert eta is None  # division by zero speed must not happen / must not crash


def test_even_rounds_odd_down():
    assert proxy_generation._even(1921) == 1920
    assert proxy_generation._even(1081) == 1080


def test_even_leaves_even_untouched():
    assert proxy_generation._even(1920) == 1920
    assert proxy_generation._even(0) == 0
