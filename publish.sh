#!/usr/bin/env bash
# publish.sh
# 만든 자막과 화면 파일을 GitHub에 올린다. 몇 분 뒤 GitHub Pages 주소에 반영된다.
# 사용 예: bash publish.sh
set -e
cd "$(dirname "$0")"

git add index.html subs make_subs.py run.sh publish.sh requirements.txt .gitignore

if git diff --cached --quiet; then
  echo "새로 올릴 변경 사항이 없습니다."
  exit 0
fi

COUNT=$(find subs -name '*.json' ! -name 'index.json' | wc -l)
git commit -q -m "자막 업데이트: 총 ${COUNT}편 ($(date '+%Y-%m-%d %H:%M'))"
git push -u origin main
echo "올리기 완료: 자막 총 ${COUNT}편. 1~2분 뒤 차량 브라우저에서 새로고침하세요."
