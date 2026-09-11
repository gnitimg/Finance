#!/usr/bin/env bash
set -euo pipefail

SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 -m venv "${SKILL_ROOT}/.venv"
"${SKILL_ROOT}/.venv/bin/python" -m pip install --upgrade pip
if grep -Eq '^[[:space:]]*[^#[:space:]]' "${SKILL_ROOT}/requirements.txt"; then
  "${SKILL_ROOT}/.venv/bin/pip" install -r "${SKILL_ROOT}/requirements.txt"
fi
chmod +x "${SKILL_ROOT}/scripts/finance.py" "${SKILL_ROOT}/tools/finance_skill_launcher.py"
echo "Finance Skill ready: ${SKILL_ROOT}/.venv/bin/python ${SKILL_ROOT}/scripts/finance.py health --pretty"
