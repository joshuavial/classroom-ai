#!/bin/bash
# usage: check-names.sh < names (one per line). Prints name, pypi, npm, gh-account, gh-repos-exact-name, top-stars
code(){ curl -s -o /dev/null -w '%{http_code}' "$1"; }
while read -r n; do
  [ -z "$n" ] && continue
  p=$( [ "$(code https://pypi.org/pypi/$n/json)" = 200 ] && echo TAKEN || echo free)
  m=$( [ "$(code https://registry.npmjs.org/$n)" = 200 ] && echo TAKEN || echo free)
  u=$(gh api users/$n >/dev/null 2>&1 && echo TAKEN || echo free)
  r=$(gh api -X GET search/repositories -f q="$n in:name" -f sort=stars -f per_page=100 \
      --jq "[.items[] | select(.name|ascii_downcase==\"$n\")] | \"\(length) \(map(.stargazers_count)|max // 0)\"" 2>/dev/null)
  printf '%s\tpypi:%s\tnpm:%s\tgh-user:%s\tgh-repos(n maxstars):%s\n' "$n" "$p" "$m" "$u" "$r"
  sleep 2.5  # ponytail: search API 30/min
done
