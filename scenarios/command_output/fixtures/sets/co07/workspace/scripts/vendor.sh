#!/bin/sh
# third_party/retrykit is a separate clone of the upstream project, pinned to a tag.
# It is not committed here; this script fetches it.
set -e
git clone https://git.example.org/retrykit.git third_party/retrykit
git -C third_party/retrykit checkout v1.8.2
