import collections
import logging

from atex import util
from atex.orchestrator.adhoc import AdHocOrchestrator

logger = logging.getLogger("contest")


# copy/pasted from the Contest repo, lib/virt.py
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


class ContestOrchestrator(AdHocOrchestrator):
    """
    Orchestrator for the Contest test suite:
    https://github.com/RHSecurityCompliance/contest

    Includes SCAP content upload via rsync and other Contest-specific
    optimizations (around VM snapshots and scheduling).

    **Supports FMFExecutor only.**
    """
    content_dir_on_remote = "/root/upstream-content"

    def __init__(self, *args, content_dir, fmf_tests, max_reruns=1, **kwargs):
        """
        - `content_dir` is a filesystem path to ComplianceAsCode/content local
          directory, to be uploaded to the tested systems.

        - `fmf_tests` is an atex.executor.fmf.FMFTests instance that was also
          passed to the `executor` argument (factory function).

        - `max_reruns` is an integer of how many times to re-try running
          a failed test (which exited with non-0 or caused an Executor
          exception).
        """
        super().__init__(*args, **kwargs)
        self.content_dir = content_dir
        self.fmf_tests = fmf_tests
        # indexed by test name, value being integer of how many times
        self.reruns = collections.defaultdict(lambda: max_reruns)

        # TODO: check (probably below) that a created Executor instance is
        #       FMFExecutor, because we rely on exit code being the return
        #       value of run_test() here

    def run_setup(self, info):
        super().run_setup(info)
        # upload pre-built content
        info.remote.rsync(
            "-r", "--delete", "--exclude=.git/",
            f"{self.content_dir}/",
            f"remote:{self.content_dir_on_remote}",
            func=util.subprocess_log,
        )

    def next_test(self, to_run, previous):
        all_tests = self.fmf_tests.tests
        # fresh remote, prefer running destructive tests (which likely need
        # clean OS) to get them out of the way and prevent them from running
        # on a tainted OS later
        if type(previous) is AdHocOrchestrator.SetupInfo:
            for next_name in to_run:
                next_tags = all_tests[next_name].get("tag", ())
                logger.debug(f"considering next_test for destructivity: {next_name}")
                if "destructive" in next_tags:
                    logger.debug(f"chosen next_test: {next_name}")
                    return next_name

        # previous test was run and finished non-destructively,
        # try to find a next test with the same Contest lib.virt guest tags
        # as the previous one, allowing snapshot reuse by Contest
        elif type(previous) is AdHocOrchestrator.FinishedInfo:
            finished_tags = all_tests[previous.test_name].get("tag", ())
            logger.debug(f"previous finished test on {previous.remote}: {previous.test_name}")
            # if Guest tag is None, don't bother searching
            if finished_guest_tag := calculate_guest_tag(finished_tags):
                for next_name in to_run:
                    logger.debug(f"considering next_test with tags {finished_tags}: {next_name}")
                    next_tags = all_tests[next_name].get("tag", ())
                    next_guest_tag = calculate_guest_tag(next_tags)
                    if next_guest_tag and finished_guest_tag == next_guest_tag:
                        logger.debug(f"chosen next_test: {next_name}")
                        return next_name

        # try to prioritize important tests (or ones that rerun often)
        return max(to_run, key=lambda test: all_tests[test].get("extra-priority", 0))

    def destructive(self, info):
        # if the test returned non-0 exit code, it could have thrown
        # a python exception of its own, or (if bash) aborted abruptly
        # due to 'set -e', don't trust the remote, consider it destroyed
        # (0 = pass, 2 = fail, anything else = bad)
        if info.exit_code not in [0,2]:
            return True

        # if the test was destructive, assume the remote is destroyed
        test_data = self.fmf_tests.tests[info.test_name]
        tags = test_data.get("tag", ())
        if "destructive" in tags:
            return True

        return False

    def should_be_rerun(self, info):
        remote_with_test = f"{info.remote}: '{info.test_name}'"

        reruns_left = self.reruns[info.test_name]
        logger.info(f"{remote_with_test}: {reruns_left} reruns left")
        if reruns_left > 0:
            self.reruns[info.test_name] -= 1
            return True
        else:
            return False
