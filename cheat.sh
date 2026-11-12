#!/data/data/com.termux/files/usr/bin/bash

emulate -L zsh
setopt err_return no_unset pipe_fail

# Use zsh builtins for mkdir/mv/rm so we don't fork for them
zmodload -F zsh/files b:mkdir b:mv b:rm 2>/dev/null || true

if (( $# == 0 )); then
  print -u2 "Example usage: 'cheat rg'"
  return 1
fi

typeset -r cache_dir=${CHEAT_DIR:-$HOME/.cheats}
typeset -r query=${1// /+}                 # cheat.sh uses + for spaces
typeset -r safe=${query//[^A-Za-z0-9._+-]/_}   # filesystem-safe filename
typeset -r cached=$cache_dir/$safe

# 1) Serve from cache if present and non-empty
if [[ -s $cached ]]; then
  print -r -- "$(<$cached)"                # $(<file) — no cat fork
  return 0
fi

# 2) Cache miss — fetch, write atomically, then show
[[ -d $cache_dir ]] || mkdir -p -- $cache_dir

typeset -r tmpfile=$cache_dir/.${safe}.$$.part

if ! curl -fsSL --compressed --max-time 15 "https://cheat.sh/$query" -o $tmpfile; then
  print -u2 "cheat: failed to fetch '$query'"
  rm -f -- $tmpfile
  return 1
fi

if [[ ! -s $tmpfile ]]; then
  print -u2 "cheat: empty response for '$query'"
  rm -f -- $tmpfile
  return 1
fi

mv -- $tmpfile $cached
print -r -- "$(<$cached)"
