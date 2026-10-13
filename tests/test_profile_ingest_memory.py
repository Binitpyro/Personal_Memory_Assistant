"""A9-13: the memory profiler reads Linux /proc/self/status, not just Windows."""

from scripts.profile_ingest_memory import _proc_status_reader


def test_proc_status_reader_parses_rss_and_peak(tmp_path):
    status = tmp_path / "status"
    status.write_text(
        "Name:\tpython\nVmPeak:\t 9999999 kB\nVmHWM:\t  524288 kB\nVmRSS:\t  262144 kB\nThreads:\t12\n"
    )
    assert _proc_status_reader(status)() == (256.0, 512.0)


def test_proc_status_reader_missing_fields_read_zero(tmp_path):
    status = tmp_path / "status"
    status.write_text("Name:\tpython\n")
    assert _proc_status_reader(status)() == (0.0, 0.0)
