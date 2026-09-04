#!/usr/bin/python3

import atexit
import collections
import contextlib
import logging
import lzma
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from atex.aggregator.jsonl import LZMAJSONLinesAggregator
from atex.executor.fmf import FMFExecutor, discover, duration_to_seconds
from atex.orchestrator import adhoc
from atex.provisioner.podman import SystemdPodmanProvisioner

# from tmt plan
contest = os.environ["CONTEST_DIR"]

# from Packit env / TF env
plan = os.environ.get("PLAN", "/plans/daily")
reruns = int(os.environ.get("RERUNS", "1"))
if (test_names := os.environ.get("TESTS")) is not None:
    test_names = test_names.split(",")
    test_excludes = None
else:
    # if TESTS was not given
    test_excludes = (
        # can't run inside a container
        "/hardening/image-builder",
        "/hardening/container/bootc-image-builder",
        "/host-os/",
        # doesn't make sense for upstream centos stream
        "/scanning/disa-alignment",
        # works with un-namespaced kernel audit subsystem
        "/scanning/audit-rules-syscalls-grouping",
    )

# parse valid CentOS Streams from env variables
streams = set()
for key in os.environ:
    if key.startswith("CONTENT_DIR_"):
        streams.add(key.removeprefix("CONTENT_DIR_"))
if not streams:
    raise RuntimeError("no CONTENT_DIR_* defined")


class ContestOrchestrator:
    def __init__(self, *args, fmf_tests, **kwargs):
        """
        - `fmf_tests` is an atex.executor.fmf.FMFTests instance that was also
          passed to the `executor` argument (factory function).
        """
        super().__init__(*args, **kwargs)

        # pre-calculate guest tags classifications
        self.__tag_idx = collections.defaultdict(set)
        self.__all_tagged_tests = set()
        for test_name in fmf_tests.data:
            tag_names = fmf_tests.data[test_name].get("tag", ())
            if tag := self._calculate_guest_tag(tag_names):
                self.__tag_idx[tag].add(test_name)
                self.__all_tagged_tests.add(test_name)

        self.__all_destructive_tests = {
            name for name, meta in fmf_tests.data.items()
            if "destructive" in meta.get("tag", ())
        }

        self.__fmf_tests = fmf_tests
        self.__seen_tags = collections.defaultdict(int)

    def _fastest(self, tests):
        meta = self.__fmf_tests.data
        return min(
            tests,
            key=lambda name: duration_to_seconds(meta[name].get("duration", "5m")),
        )

    # copy/pasted from the Contest repo, lib/virt.py
    @staticmethod
    def _calculate_guest_tag(tags):
        if "snapshottable" not in tags:
            return None
        name = "default"
        if "with-gui" in tags:
            name += "_gui"
        if "uefi" in tags:
            name += "_uefi"
        if "fips" in tags:
            name += "_fips"
        return name

    def next_test(self, to_run, previous, /):
        all_tests = self.__fmf_tests.data
        # fresh remote, prefer running destructive tests (which likely need
        # clean OS) to get them out of the way and prevent them from running
        # on a tainted OS later
        if type(previous) is self.SetupInfo:
            if remaining := self.__all_destructive_tests & to_run:
                return super().next_test(remaining, previous)

        # previous test was run and finished non-destructively,
        # try to find a next test with the same Contest lib.virt guest tags
        # as the previous one, allowing snapshot reuse by Contest
        elif type(previous) is self.FinishedInfo:
            # if Guest tag is None, don't bother searching
            finished_tags = all_tests[previous.test_name].get("tag", ())
            if finished_guest_tag := self._calculate_guest_tag(finished_tags):
                if remaining := self.__tag_idx[finished_guest_tag] & to_run:
                    return super().next_test(remaining, previous)

        # next, try to find a test which could prepare a snapshottable VM
        # for others (a.k.a. a "seeder"), for a tag that we've never seen
        # before (so that it can start a "snapshot reuse train" early)
        fewest_seeders = sorted(self.__tag_idx.items(), key=lambda x: self.__seen_tags[x[0]])
        for tag, tag_tests in fewest_seeders:
            # allow up to 10 sequential tests (1 seeder + 9 reuse), not more
            # as it would hurt parallelism
            if self.__seen_tags[tag] < len(tag_tests) / 10:
                if remaining := tag_tests & to_run:
                    self.__seen_tags[tag] += 1
                    # we ideally need the fastest test
                    return self._fastest(remaining)

        # as a last-ditch attempt, try to stall tagged tests, so they have
        # a chance to catch a prepared snapshottable VM from above
        if untagged := to_run - self.__all_tagged_tests:
            return super().next_test(untagged, previous)

        # fall back to the base class or a mixin
        return super().next_test(to_run, previous)

    def destructive(self, info, /):
        # 2 is a valid result with oscap finding failures
        if info.exit_code not in [0,2]:
            return True

        # if the test was destructive, assume the remote is destroyed
        test_data = self.__fmf_tests.data[info.test_name]
        tags = test_data.get("tag", ())
        if "destructive" in tags:
            return True

        return False


# filter (show) only messages from the atex.orchestrator logger,
# show all warnings (and above) from everywhere,
# show all from the root logger
class OrchestratorLogFilter(logging.Filter):
    @staticmethod
    def filter(record):
        return (
            record.levelno >= logging.WARNING
            or record.name.startswith("atex.orchestrator")
            or record.name == "root"
        )


# log brief info to console (to not overload 5MB Gitlab console log),
# but be verbose in a separate file-based log (uploaded as artifact)
#
# console - keep it brief, just basic orchestration + warnings
console_log = logging.StreamHandler(sys.stderr)
console_log.setLevel(logging.INFO)
console_log.addFilter(OrchestratorLogFilter())
console_log.setFormatter(logging.Formatter(
    fmt="%(asctime)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
# debug log - store ALL debugging info, compressed
tmt_test_data = Path(os.environ["TMT_TEST_DATA"])
debug_log_fobj = lzma.open(tmt_test_data / "test-debug.log.xz", "wt")
atexit.register(debug_log_fobj.close)
file_log = logging.StreamHandler(debug_log_fobj)
file_log.setLevel(logging.DEBUG)
file_log.setFormatter(logging.Formatter(
    fmt="%(asctime)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
logging.basicConfig(
    level=logging.DEBUG,
    handlers=(console_log, file_log),
    force=True,  # use just the handlers above
)


def format_statistics(pairs):
    def system_stats():
        r = subprocess.run(
            ["vmstat", "-S", "M", "5", "2"],
            capture_output=True, text=True,
        )
        lines = r.stdout.strip().splitlines()
        return lines[1], lines[-1]

    def storage_stats():
        r = subprocess.run(
            ["df", "-h", "--output=size,used,avail,pcent,target", "/var/lib/containers"],
            capture_output=True, text=True,
        )
        return r.stdout.strip().splitlines()

    def gen():
        for orch, prov in pairs:
            yield str(prov)
            yield str(orch)
        yield from system_stats()
        yield from storage_stats()

    return "\n".join(gen())


def abort_on_signal(signum, _):
    logging.error(f"got signal {signum}, aborting")
    raise SystemExit(1)


signal.signal(signal.SIGTERM, abort_on_signal)
signal.signal(signal.SIGHUP, abort_on_signal)

with contextlib.ExitStack() as stack:
    runs = Path("runs")
    runs.mkdir()
    aggregator = LZMAJSONLinesAggregator(
        runs / "results.json.xz",
        runs / "files",
    )
    stack.enter_context(aggregator)

    old_runs = Path("old_runs")
    old_runs.mkdir()
    old_aggregator = LZMAJSONLinesAggregator(
        old_runs / "results.json.xz",
        old_runs / "files",
        allow_duplicate=True,
    )
    stack.enter_context(old_aggregator)

    running = set()

    for stream in streams:
        platform_name = f"cs{stream}"

        logging.info(f"cs{stream}: discovering with plan '{plan}' and excludes: {test_excludes}")
        fmf_tests = discover(
            contest,
            plan,
            excludes=test_excludes,
            context={
                "distro": f"centos-stream-{stream}",
                "arch": platform.machine(),
            },
            libraries=False,
        )
        logging.info(f"cs{stream}: initially discovered tests:\n{fmf_tests.data.keys()}")

        # limit tests to just the ones specified via CI inputs
        # (we can't use discover(names=...) because it merges the names
        #  to the ones specified in the plan)
        if test_names:
            logging.info(f"cs{stream}: filtering tests to: {test_names}")
            compiled = tuple(re.compile(pattern) for pattern in test_names)
            fmf_tests.data = {
                name: data for name, data in fmf_tests.data.items()
                if any(pattern.search(name) for pattern in compiled)
            }
            logging.info(f"cs{stream}: discovered tests after filtering:\n{fmf_tests.data.keys()}")

        if not fmf_tests.data:
            logging.info(f"cs{stream}: SKIPPING, NO TESTS")
            continue

        provisioner = SystemdPodmanProvisioner(
            image=platform_name,
            run_options=(
                # these are explicitly allowing all capabilities and syscalls,
                # but always isolated to a userns, netns, etc.
                # - basically simulating a light-weight virtual machine that has access
                #   to low-level OS functionality, but within namespace boundaries
                "--userns", "auto:size=1048576",  # slice out of 2^31 subuid, default 1024
                "--cap-add", "all",
                "--security-opt", "seccomp=unconfined",
                "--security-opt", "label=disable",
                "--security-opt", "unmask=ALL",
                # both these do namespaced access by default, so we can safely pass them
                "--device", "/dev/kvm",
                "--device", "/dev/net/tun",
                # needed for fuse-overlayfs
                "--device", "/dev/fuse",
            ),
            max_remotes=15,  # this is per centos-stream !
            isolate=True,
        )
        stack.enter_context(provisioner)

        class PerStreamOrchestrator(
            ContestOrchestrator,
            adhoc.FMFPriorityMixin(fmf_tests),
            adhoc.FMFDurationMixin(fmf_tests),
            adhoc.LimitedRerunsMixin(reruns),
            adhoc.AdHocOrchestrator,
        ):
            pass

        orchestrator = PerStreamOrchestrator(
            platform=platform_name,
            tests=fmf_tests.data,
            provisioners=(provisioner,),
            aggregator=aggregator,
            executor=lambda conn, tests=fmf_tests: FMFExecutor(
                conn,
                fmf_tests=tests,
                # embedded inside the container image
                env={"CONTEST_CONTENT": os.environ["CONTENT_IN_IMAGE"]},
            ),
            old_aggregator=old_aggregator,
            fmf_tests=fmf_tests,
        )

        running.add((orchestrator, provisioner))

    if not running:
        raise RuntimeError("no orchestrators configured")

    for orch, _ in running:
        stack.enter_context(orch)

    next_writeout = time.monotonic() + 60
    while running:
        time.sleep(0.1)

        if time.monotonic() > next_writeout:
            logging.info(f"STATISTICS:\n{format_statistics(running)}")
            next_writeout = time.monotonic() + 60

        finished = {(orch, prov) for orch, prov in running if not orch.serve_once()}

        for _, prov in finished:
            prov.stop()  # release reservation-in-progress remotes immediately

        running.difference_update(finished)

# if old_runs is empty (not a single result in the JSON), delete the folder
with lzma.open(old_runs / "results.json.xz", "rb") as f:
    is_empty = f.read(1) == b""
if is_empty:
    shutil.rmtree(old_runs)
