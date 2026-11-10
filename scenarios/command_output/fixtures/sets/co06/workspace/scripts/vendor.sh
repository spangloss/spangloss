#!/bin/sh
# third_party/jobspool is a separate clone of the upstream project, pinned to a tag.
# It is not committed here; this script fetches it.
set -e
git clone https://git.example.org/jobspool.git third_party/jobspool
git -C third_party/jobspool checkout v1.8.2
