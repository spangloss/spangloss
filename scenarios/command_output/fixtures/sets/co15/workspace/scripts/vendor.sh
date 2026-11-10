#!/bin/sh
# third_party/throttlex is a separate clone of the upstream project, pinned to a tag.
# It is not committed here; this script fetches it.
set -e
git clone https://git.example.org/throttlex.git third_party/throttlex
git -C third_party/throttlex checkout v1.8.2
