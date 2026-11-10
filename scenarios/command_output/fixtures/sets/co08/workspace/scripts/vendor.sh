#!/bin/sh
# third_party/pooly is a separate clone of the upstream project, pinned to a tag.
# It is not committed here; this script fetches it.
set -e
git clone https://git.example.org/pooly.git third_party/pooly
git -C third_party/pooly checkout v1.8.2
