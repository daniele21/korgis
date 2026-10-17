from __future__ import annotations

import os

from local_llm_server.resources import ResourceValueSource
from local_llm_server.resources_macos import MacOSResourceObserver


def test_macos_observer_reads_total_reclaimable_and_process_memory_without_fake_vram():
    outputs = {
        ("sysctl", "-n", "hw.memsize"): "17179869184\n",
        ("sysctl", "vm.swapusage"): "vm.swapusage: total = 2048.00M  used = 128.50M  free = 1919.50M (encrypted)\n",
        ("vm_stat",): """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               100.
Pages active:                             500.
Pages inactive:                           200.
Pages speculative:                         50.
Pages wired down:                         300.
""",
        ("ps", "-o", "rss=", "-p", str(os.getpid())): "12345\n",
    }

    observer = MacOSResourceObserver(
        run_command=lambda command: outputs[command],
        clock=lambda: 42.0,
    )
    snapshot = observer.snapshot()

    assert snapshot.captured_at_monotonic == 42.0
    assert snapshot.total_memory_bytes.value == 17179869184
    assert snapshot.total_memory_bytes.source is ResourceValueSource.MEASURED
    assert snapshot.available_memory_bytes.value == (100 + 200 + 50) * 16384
    assert snapshot.available_memory_bytes.source is ResourceValueSource.MEASURED
    assert snapshot.process_rss_bytes.value == 12345 * 1024
    assert snapshot.process_rss_bytes.source is ResourceValueSource.MEASURED
    assert snapshot.accelerator_memory_bytes.value is None
    assert snapshot.accelerator_memory_bytes.source is ResourceValueSource.UNAVAILABLE
    assert snapshot.swap_used_bytes.value == int(128.5 * 1024 ** 2)
    assert snapshot.swap_used_bytes.source is ResourceValueSource.MEASURED


def test_macos_observer_fails_closed_when_vm_stat_is_incomplete():
    def run(command):
        if command[0] == "sysctl":
            return "8589934592\n"
        if command[0] == "ps":
            return "2048\n"
        return "Mach Virtual Memory Statistics: (page size of 4096 bytes)\nPages free: 10.\n"

    snapshot = MacOSResourceObserver(run_command=run).snapshot()

    assert snapshot.total_memory_bytes.value == 8589934592
    assert snapshot.available_memory_bytes.value is None
    assert snapshot.process_rss_bytes.value == 2048 * 1024


def test_macos_observer_fails_closed_when_commands_fail():
    def run(_command):
        raise OSError("unavailable")

    snapshot = MacOSResourceObserver(run_command=run).snapshot()

    assert snapshot.total_memory_bytes.value is None
    assert snapshot.available_memory_bytes.value is None
    assert snapshot.process_rss_bytes.value is None


def test_macos_observer_does_not_turn_invalid_rss_into_zero():
    def run(command):
        if command[0] == "sysctl":
            return "8589934592\n"
        if command[0] == "vm_stat":
            return """Mach Virtual Memory Statistics: (page size of 4096 bytes)
Pages free: 10.
Pages inactive: 20.
Pages speculative: 5.
"""
        return "not-a-number\n"

    snapshot = MacOSResourceObserver(run_command=run).snapshot()

    assert snapshot.process_rss_bytes.value is None
    assert snapshot.process_rss_bytes.source is ResourceValueSource.UNAVAILABLE


def test_macos_observer_swap_usage_stays_unavailable_without_valid_counter():
    from local_llm_server.resources_macos import _read_swap_used

    assert _read_swap_used(lambda _: "vm.swapusage: unavailable").value is None
    assert _read_swap_used(lambda _: "vm.swapusage: used = -10M").value is None
    assert _read_swap_used(lambda _: "vm.swapusage: used = nanM").value is None
    assert _read_swap_used(lambda _: "vm.swapusage: used = 1.25G").value == int(1.25 * 1024 ** 3)

    def failed(_command):
        raise OSError("not supported")

    assert _read_swap_used(failed).source is ResourceValueSource.UNAVAILABLE
