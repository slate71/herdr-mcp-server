#!/bin/sh
# Plugin commands run with the herdr server's PATH, which can be as bare as
# sshd's /usr/bin:/bin when the server was spawned by a remote attach. Find uv
# here so the manifest does not depend on how herdr was started.
PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export PATH
exec uv "$@"
