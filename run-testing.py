#!/usr/bin/python3

import atexit
import contextlib
import gzip
import logging
import lzma
import os
import platform
import shutil
import signal
import sys
import time
from pathlib import Path

from atex.aggregator.json import LZMAJSONAggregator
from atex.connection.local import LocalConnection
from atex.executor.fmf import FMFExecutor, FMFTests
from atex.orchestrator import adhoc
from atex.provisioner.shvirt import SharedVirtProvisioner

# from tmt plan
contest = os.environ["CONTEST_DIR"]
ssh_key = os.environ["VM_SSH_KEY"]

# from Packit env / TF env
plan = os.environ.get("PLAN", "/plans/daily")
reruns = int(os.environ.get("RERUNS", "1"))

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
        self.fmf_tests = fmf_tests

    # copy/pasted from the Contest repo, lib/virt.py
    @staticmethod
    def calculate_guest_tag(tags):
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

    def next_test(self, to_run, previous):
        all_tests = self.fmf_tests.tests
        # fresh remote, prefer running destructive tests (which likely need
        # clean OS) to get them out of the way and prevent them from running
        # on a tainted OS later
        if type(previous) is self.SetupInfo:
            for next_name in to_run:
                next_tags = all_tests[next_name].get("tag", ())
                logging.debug(f"considering next_test for destructivity: {next_name}")
                if "destructive" in next_tags:
                    logging.debug(f"chosen next_test: {next_name}")
                    return next_name

        # previous test was run and finished non-destructively,
        # try to find a next test with the same Contest lib.virt guest tags
        # as the previous one, allowing snapshot reuse by Contest
        elif type(previous) is self.FinishedInfo:
            finished_tags = all_tests[previous.test_name].get("tag", ())
            logging.debug(f"previous finished test on {previous.remote}: {previous.test_name}")
            # if Guest tag is None, don't bother searching
            if finished_guest_tag := self.calculate_guest_tag(finished_tags):
                for next_name in to_run:
                    logging.debug(f"considering next_test with tags {finished_tags}: {next_name}")
                    next_tags = all_tests[next_name].get("tag", ())
                    next_guest_tag = self.calculate_guest_tag(next_tags)
                    if next_guest_tag and finished_guest_tag == next_guest_tag:
                        logging.debug(f"chosen next_test: {next_name}")
                        return next_name

        return super().next_test(to_run, previous)

    def destructive(self, info):
        # 2 is a valid result with oscap finding failures
        if info.exit_code not in [0,2]:
            return True

        # if the test was destructive, assume the remote is destroyed
        test_data = self.fmf_tests.tests[info.test_name]
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
debug_log_fobj = lzma.open("runcontest.txt.xz", "wt")
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


def abort_on_signal(signum, _):
    logging.error(f"got signal {signum}, aborting")
    raise SystemExit(1)


signal.signal(signal.SIGTERM, abort_on_signal)
signal.signal(signal.SIGHUP, abort_on_signal)

with contextlib.ExitStack() as stack:
    runs = Path("runs")
    runs.mkdir()
    aggregator = LZMAJSONAggregator(
        runs / "results.json.xz",
        runs / "files",
    )
    stack.enter_context(aggregator)

    old_runs = Path("old_runs")
    old_runs.mkdir()
    old_aggregator = LZMAJSONAggregator(
        old_runs / "results.json.gz",
        old_runs / "files",
        allow_duplicate=True,
    )
    stack.enter_context(old_aggregator)

    # one shared by all
    localhost_conn = LocalConnection()
    stack.enter_context(localhost_conn)

    orchestrators = set()

    for stream in streams:
        platform_name = f"cs{stream}"

        provisioner = SharedVirtProvisioner(
            host=localhost_conn,
            image=platform_name,  # same as platform, see main.fmf
            domain_sshkey=ssh_key,
            domain_host="127.0.0.1",
            reserve_name=f"{platform_name} testing",
        )

        fmf_tests = FMFTests(
            contest,
            plan,
            context={
                "distro": f"centos-stream-{stream}",
                "arch": platform.machine(),
            },
        )

        class PerStreamOrchestrator(
            ContestOrchestrator,
            adhoc.FMFPriorityMixin(fmf_tests),
            adhoc.LimitedRerunsMixin(reruns),
            adhoc.AdHocOrchestrator,
        ):
            pass

        orchestrator = PerStreamOrchestrator(
            platform=platform_name,
            tests=fmf_tests.tests.keys(),
            provisioners=(provisioner,),
            aggregator=aggregator,
            executor=lambda conn, tests=fmf_tests: FMFExecutor(
                conn,
                fmf_tests=tests,
                # embedded inside the VM image by virt-copy-in
                env={"CONTEST_CONTENT": "/root/content"},
            ),
            old_aggregator=old_aggregator,
            fmf_tests=fmf_tests,
        )
        stack.enter_context(orchestrator)

        orchestrators.add(orchestrator)

    if not orchestrators:
        raise RuntimeError("no orchestrators configured")

    next_writeout = time.monotonic() + 300
    while orchestrators:
        finished = {o for o in orchestrators if o.serve_once()}
        if time.monotonic() > next_writeout:
            statuses = "  " + "\n  ".join(str(o) for o in orchestrators)
            logging.info(f"STATISTICS:\n{statuses}")
            next_writeout = time.monotonic() + 300
        time.sleep(0.1)

# if old_runs is empty (not a single result in the JSON), delete the folder
with gzip.open(old_runs / "results.json.gz", "rb") as f:
    is_empty = f.read(1) == b""
if is_empty:
    shutil.rmtree(old_runs)
