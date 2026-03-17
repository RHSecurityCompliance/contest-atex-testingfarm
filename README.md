# Purpose

This repo integrates [Contest](https://github.com/RHSecurityCompliance/contest),
the test suite, with [ATEX](https://github.com/RHSecurityCompliance/atex),
an inrastructure and test running framework.

It has 2 basic use cases:

## 1. Using Testing Farm as a runner

This uses [Testing Farm](https://testing-farm.io/)'s ability to run tests
in a container thanks to
[the respective tmt feature](https://tmt.readthedocs.io/en/stable/plugins/provision.html#plugins-provision-container).
This runs within a few seconds and with low overhead as it doesn't need to
actually reserve any systems from pools (Beaker, AWS, etc.).

Within this container, we run [runner/test.py](runner/test.py) to serve as
a "test controller", using ATEX to reserve the actual pool-based Testing Farm
systems, schedule tests on them, and aggregate and render final results.

The results are then stored as Testing Farm artifacts, as if they were the
results of the container-running test.

All this is very similar to running ATEX in Github Actions or Gitlab CI,
or on Jenkins, and actually mirrors how Testing Farm itself uses tmt.

```
$ testing-farm request \
    --git-url https://github.com/RHSecurityCompliance/contest-atex-testingfarm.git \
    --git-ref devel \
    --plan /runner/plan \
    -s TESTING_FARM_API_TOKEN=your-tf-token \
    -e TODO_SOME_VAR=... \
    -e TODO_SOME_VAR=... \
    -e TODO_SOME_VAR=... \
    -e TODO_SOME_VAR=... \
    -e TODO_SOME_VAR=...
```

## 2. Running on a giant VM host

This variant uses [Testing Farm](https://testing-farm.io/) to reserve one big
virtualization-capable system (VM host) (typ. 256G RAM, 64 CPU cores, etc.),
prepares a reference VM image based on the VM host's repositories, and spins up
many VMs to be used for the testing itself.

In this case, the VM host itself runs [vmhost/test.py](vmhost/test.py), the
ATEX-using script, and the final results are also uploaded to Testing Farm
artifacts from this VM host.

(IOW there is no separate ATEX runner that would reserve a VM host, it all
happens on the host itself.)

To use it, prefer Packit and manual trigger via `/packit -i contest-full`:

```
- job: tests
  trigger: pull_request
  identifier: contest-full
  targets: [fedora-latest-stable]
  manual_trigger: true
  skip_build: true
  fmf_url: https://github.com/RHSecurityCompliance/contest-atex-testingfarm.git
  fmf_ref: devel
  tmt_plan: /vmhost/plan
```

But manual submission works too:

```
$ testing-farm request \
    --compose Fedora-latest \
    --git-url https://github.com/RHSecurityCompliance/contest-atex-testingfarm.git \
    --git-ref devel \
    --plan /vmhost/plan \
    -e PACKIT_SOURCE_URL=https://github.com/ComplianceAsCode/content.git \
    -e PACKIT_SOURCE_BRANCH=master
```

(substitute `PACKIT_SOURCE_*` for the PR submitter's repo+branch if you wish)
