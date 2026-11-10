#!/bin/sh
# third_party/drainer is a separate clone of the upstream project, pinned to a tag.
# It is not committed here; this script fetches it.
set -e
git clone https://git.example.org/drainer.git third_party/drainer
git -C third_party/drainer checkout v1.8.2
