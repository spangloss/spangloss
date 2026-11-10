#!/bin/sh
# third_party/lrumini is a separate clone of the upstream project, pinned to a tag.
# It is not committed here; this script fetches it.
set -e
git clone https://git.example.org/lrumini.git third_party/lrumini
git -C third_party/lrumini checkout v1.8.2
