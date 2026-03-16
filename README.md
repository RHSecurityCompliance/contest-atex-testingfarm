This uses [Testing Farm](https://testing-farm.io/)'s ability to run tests
in a container thanks to
[the respective tmt feature](https://tmt.readthedocs.io/en/stable/plugins/provision.html#plugins-provision-container).
This runs within a few seconds and with low overhead as it doesn't need to
actually reserve any systems from pools (Beaker, AWS, etc.).

Within this container, we run [test.py](test.py) to server as a "test
controller", using ATEX to reserve the actual pool-based Testing Farm systems,
schedule tests on them, and aggregate and render final results.

The results are then stored as Testing Farm artifacts, as if they were the
results of the container-running test.

All this is very similar to running ATEX in Github Actions or Gitlab CI,
or on Jenkins, and actually mirrors how Testing Farm itself uses tmt.
