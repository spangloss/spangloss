#!/bin/sh
# third_party/taskring is a separate clone of the upstream project, pinned to a tag.
# It is not committed here; this script fetches it.
set -e
git clone https://git.example.org/taskring.git third_party/taskring
git -C third_party/taskring checkout v1.8.2
