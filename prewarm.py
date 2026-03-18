#!/usr/bin/env python3
#
# prewarm the data of a sparse file into page cache, ignoring holes

import os
import sys

CHUNK = 1 << 20  # 1 MiB

fd = os.open(sys.argv[1], os.O_RDONLY)
size = os.fstat(fd).st_size

offset = 0
while offset < size:
    try:
        data_start = os.lseek(fd, offset, os.SEEK_DATA)
    except OSError:
        break
    try:
        hole_start = os.lseek(fd, data_start, os.SEEK_HOLE)
    except OSError:
        hole_start = size

    os.lseek(fd, data_start, os.SEEK_SET)
    remaining = hole_start - data_start
    while remaining > 0:
        n = min(CHUNK, remaining)
        os.read(fd, n)
        remaining -= n

    offset = hole_start

os.close(fd)
