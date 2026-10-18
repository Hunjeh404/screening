#!/usr/bin/env bash
# 시장 데이터와 공유 이력을 함께 커밋한다. 동시 소스 변경은 재배치 후 반영한다.
set -euo pipefail
market="${1:?us or kr}"
if [[ "$market" == us ]]; then data_file=docs/data.json; else data_file=docs/data_kr.json; fi
git config user.name "github-actions"
git config user.email "github-actions@users.noreply.github.com"
git add "$data_file" docs/card_history.json
git diff --cached --quiet && exit 0
git commit -m "data: ${market} cards and history $(date +%F)"
for attempt in 1 2 3; do
  if git push; then exit 0; fi
  git pull --rebase
 done
exit 1
