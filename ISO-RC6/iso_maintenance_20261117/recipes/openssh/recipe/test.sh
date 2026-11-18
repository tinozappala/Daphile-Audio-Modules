#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
export LD_LIBRARY_PATH="$PWD/sdk/lib:$(realpath ../../python313/rc5-full/lib)"
export LD_BIND_NOW=1
python3 smoke.py > smoke.json
cd openssh-10.6p2
tests=(crypto kex sshkey sshsig sshbuf misc)
targets=()
for group in "${tests[@]}"; do targets+=("regress/unittests/$group/test_$group"); done
{ time timeout -k 5 70 make -j4 "${targets[@]}"; } > ../upstream.log 2>&1
for test in "${targets[@]}"; do
  d="$(dirname "$test")/testdata"
  options=()
  if [ -d "$d" ]; then options=(-d "$d"); fi
  code=0
  timeout -k 3 35 "$test" "${options[@]}" >> ../upstream.log 2>&1 || code=$?
  printf '\nRESULT %s %s\n' "$(basename "$test")" "$code" >> ../upstream.log
  if [ "$code" != 0 ] && [ "$code" != 124 ]; then exit "$code"; fi
done
