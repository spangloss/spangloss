#!/bin/sh
# third_party/slidewin is a separate clone of the upstream project, pinned to a tag.
# It is not committed here; this script fetches it.
set -e
git clone https://git.example.org/slidewin.git third_party/slidewin
git -C third_party/slidewin checkout v1.8.2
