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
from atex.provisioner.shvirt import SharedVirtProvisioner

from contest_atex import ContestOrchestrator

# from tmt plan
contest = os.environ["CONTEST_DIR"]
ssh_key = os.environ["VM_SSH_KEY"]

# from Packit env / TF env
plan = os.environ.get("PLAN", "/plans/daily")
reruns = int(os.environ.get("RERUNS", "1"))

# parse built content dirs from tmt plan env
content = {}
for key, value in os.environ.items():
    if key.startswith("CONTENT_DIR_"):
        stream = key.removeprefix("CONTENT_DIR_")
        content[stream] = value
if not content:
    raise RuntimeError("no CONTENT_DIR_* defined")


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

    for stream, content_dir in content.items():
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

        orchestrator = ContestOrchestrator(
            platform=platform_name,
            tests=fmf_tests.tests.keys(),
            provisioners=(provisioner,),
            aggregator=aggregator,
            executor=lambda conn, tests=fmf_tests: FMFExecutor(conn, fmf_tests=tests),
            old_aggregator=old_aggregator,
            content_dir=content_dir,
            fmf_tests=fmf_tests,
            max_reruns=reruns,
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
