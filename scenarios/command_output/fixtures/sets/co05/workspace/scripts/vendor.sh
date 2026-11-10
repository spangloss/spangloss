#!/bin/sh
# third_party/backoffy is a separate clone of the upstream project, pinned to a tag.
# It is not committed here; this script fetches it.
set -e
git clone https://git.example.org/backoffy.git third_party/backoffy
git -C third_party/backoffy checkout v1.8.2
