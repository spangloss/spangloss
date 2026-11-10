#!/bin/sh
# third_party/cronlet is a separate clone of the upstream project, pinned to a tag.
# It is not committed here; this script fetches it.
set -e
git clone https://git.example.org/cronlet.git third_party/cronlet
git -C third_party/cronlet checkout v1.8.2
