#!/bin/sh
# third_party/workfeed is a separate clone of the upstream project, pinned to a tag.
# It is not committed here; this script fetches it.
set -e
git clone https://git.example.org/workfeed.git third_party/workfeed
git -C third_party/workfeed checkout v1.8.2
