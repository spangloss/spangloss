#!/bin/sh
# third_party/ratelim is a separate clone of the upstream project, pinned to a tag.
# It is not committed here; this script fetches it.
set -e
git clone https://git.example.org/ratelim.git third_party/ratelim
git -C third_party/ratelim checkout v1.8.2
