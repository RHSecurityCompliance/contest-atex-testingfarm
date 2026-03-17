# Purpose

This repo integrates [Contest](https://github.com/RHSecurityCompliance/contest),
the test suite, with [ATEX](https://github.com/RHSecurityCompliance/atex),
an inrastructure and test running framework.

It uses [Testing Farm](https://testing-farm.io/) to reserve one big
virtualization-capable system (VM host) (typ. 256G RAM, 64 CPU cores, etc.),
prepares a reference VM image based on the VM host's repositories, and spins up
many VMs to be used for the testing itself.

In this case, the VM host itself runs [main.fmf](main.fmf) to prepare the host,
run all the testing and render results, uploading them as Testing Farm artifacts
of this exact test (tmt plan).

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
```

But manual submission works too:

```
$ testing-farm request \
    --compose Fedora-latest \
    --git-url https://github.com/RHSecurityCompliance/contest-atex-testingfarm.git \
    --git-ref devel \
    -e PACKIT_SOURCE_URL=https://github.com/ComplianceAsCode/content.git \
    -e PACKIT_SOURCE_BRANCH=master
```

(substitute `PACKIT_SOURCE_*` for the PR submitter's repo+branch if you wish)
