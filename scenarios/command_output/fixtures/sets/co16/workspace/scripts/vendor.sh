#!/bin/sh
# third_party/quorumq is a separate clone of the upstream project, pinned to a tag.
# It is not committed here; this script fetches it.
set -e
git clone https://git.example.org/quorumq.git third_party/quorumq
git -C third_party/quorumq checkout v1.8.2
