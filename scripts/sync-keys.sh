#!/bin/sh
# Mirror the GitHub keys of everyone in the repo's `users` file (on main) into
# sish's authorized-keys directory. sish reloads the directory on its own.
# Any fetch failure aborts before rsync, so a GitHub blip never wipes keys.
set -eu
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -fsS https://raw.githubusercontent.com/nadahlberg/flp-tunnel/main/users >"$tmp/users"
grep -v '^#' "$tmp/users" | while read -r user; do
	[ -z "$user" ] && continue
	curl -fsS "https://github.com/$user.keys" >"$tmp/$user.pub" </dev/null
done
mkdir -p /opt/tunnel/pubkeys
rsync -a --delete --exclude users "$tmp/" /opt/tunnel/pubkeys/
