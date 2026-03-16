#!/usr/bin/python3

import importlib.resources
import os
import shutil

helper = importlib.resources.files("atex.provisioner.shvirt").joinpath("atex-virt-helper")

fd = os.open("/usr/local/bin/atex-virt-helper", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o755)

with helper.open() as src, os.fdopen(fd, "w") as dst:
    shutil.copyfileobj(src, dst)
