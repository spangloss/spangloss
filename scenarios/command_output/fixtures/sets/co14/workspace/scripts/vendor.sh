#!/bin/sh
# third_party/eventbox is a separate clone of the upstream project, pinned to a tag.
# It is not committed here; this script fetches it.
set -e
git clone https://git.example.org/eventbox.git third_party/eventbox
git -C third_party/eventbox checkout v1.8.2
