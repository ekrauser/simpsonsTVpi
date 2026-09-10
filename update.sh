#!/usr/bin/env bash
# Pull the latest scripts/playlists and restart the player if anything changed.
# Run by hand, or nightly via tvupdate.timer.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

before=$(git rev-parse HEAD)
git fetch --quiet origin
git merge --ff-only --quiet "origin/$(git rev-parse --abbrev-ref HEAD)" || {
    echo "update: local branch has diverged from origin; not touching it" >&2
    exit 1
}
after=$(git rev-parse HEAD)

if [ "$before" != "$after" ]; then
    echo "update: $before -> $after"
    git --no-pager log --oneline "$before..$after"
    sudo systemctl restart tvplayer tvbutton
else
    echo "update: already up to date"
fi
