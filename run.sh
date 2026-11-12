#!/usr/bin/env bash
# run.sh
# WSL(리눅스)에서 GPU 라이브러리(cuBLAS, cuDNN) 경로를 잡아 준 뒤 make_subs.py를 실행한다.
# 사용 예: bash run.sh --limit 1
set -e
cd "$(dirname "$0")"

# pip로 설치한 nvidia-* 패키지의 lib 폴더들을 모두 찾는다.
LIBS=$(python -c '
import glob, os, nvidia
roots = list(nvidia.__path__)
libs = sorted({os.path.realpath(p) for r in roots for p in glob.glob(os.path.join(r, "*", "lib"))})
print(":".join(libs))
')

if [ -z "$LIBS" ]; then
  echo "엔비디아 라이브러리를 찾지 못했습니다. 먼저 pip install -r requirements.txt 를 실행하세요."
  exit 1
fi

export LD_LIBRARY_PATH="$LIBS:${LD_LIBRARY_PATH}"

python make_subs.py "$@"
