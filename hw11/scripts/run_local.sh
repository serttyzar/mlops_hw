#!/bin/bash
# User-invoked Terminal workflow. Does not edit Codex settings or MDM policy.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
project_dir="$PWD"
export PATH="$project_dir/.tools:/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
export UV_CACHE_DIR="$project_dir/.state/uv-cache"
export MPLCONFIGDIR="$project_dir/.state/matplotlib"
export PIP_CACHE_DIR="$project_dir/.state/pip-cache"
mkdir -p results .state
output_dir="$project_dir/results/terminal-$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$output_dir"
printf '%s\n' "$output_dir" > results/LAST_RUN.txt
exec > >(tee -a "$output_dir/launcher.log") 2>&1
stage=initialization
python_bin=''
finish() {
    result=$?
    trap - EXIT
    set +e
    if [ "$result" -ne 0 ]; then
        printf '{"state":"failed","stage":"%s","exit_code":%s}\n' "$stage" "$result" > "$output_dir/launcher-status.json"
        printf '\nОстановка на этапе: %s (код %s). Данные сохранены.\n' "$stage" "$result"
        if [ -n "$python_bin" ]; then
            "$python_bin" scripts/build_report.py --results "$output_dir" --output "$output_dir/report.html"
        fi
    else
        printf '{"state":"complete","stage":"done","exit_code":0}\n' > "$output_dir/launcher-status.json"
        printf '\nВсе проверки полного прогона прошли.\n'
    fi
    printf '\nРезультаты: %s\n' "$output_dir"
    printf 'Вернитесь в чат и напишите «запуск завершён» — агент прочитает журналы из проекта.\n'
    if [ -t 0 ]; then
        read -r -p 'Нажмите Enter, чтобы закрыть это окно. ' unused || true
    fi
    exit "$result"
}
trap finish EXIT
printf 'ДЗ 11 — локальный запуск в Terminal\n'
printf 'Начало (UTC): %s\nРезультаты: %s\n' "$(date -u +%FT%TZ)" "$output_dir"
printf 'MDM и настройки Codex не изменяются. sudo не используется.\n'
printf 'Будет использован отдельный профиль Colima mlops-hw11: 4 CPU, 6 GiB RAM.\n'
printf 'Полный цикл может занять 40–60 минут плюс загрузка образов.\n\n'
stage=python
# Reuse the earlier environment only for reading/executing installed packages.
# If it is incomplete, create a separate environment for this homework.
for candidate in "$project_dir/.venv/bin/python" "$project_dir/../hw8910/.venv/bin/python"; do
    if [ -x "$candidate" ] && "$candidate" -c 'import sys; assert sys.version_info[:2]==(3,12); import fastapi,uvicorn,httpx,prometheus_client,matplotlib' >/dev/null 2>&1; then
        python_bin="$candidate"
        break
    fi
done
if [ -z "$python_bin" ]; then
    base_python=''
    for candidate in "$project_dir/../hw8910/.venv/bin/python" /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12; do
        if [ -x "$candidate" ]; then base_python="$candidate"; break; fi
    done
    if [ -n "$base_python" ]; then
        "$base_python" -m venv .venv
    elif command -v uv >/dev/null 2>&1; then
        uv venv --python 3.12 .venv
    else
        printf 'Не найден Python 3.12 или uv. Сохраните этот журнал для агента.\n' >&2
        exit 2
    fi
    python_bin="$project_dir/.venv/bin/python"
    "$python_bin" -m pip --version >/dev/null 2>&1 || "$python_bin" -m ensurepip
    "$python_bin" -m pip install -r requirements-report.txt
fi
stage=local-tests
"$python_bin" -m unittest discover -s tests -v > "$output_dir/local-tests.log" 2>&1
printf 'Локальные тесты прошли.\n'
stage=tools
if [ ! -x .tools/kubectl ] || [ ! -x .tools/helm ] || [ ! -x .tools/kind ]; then
    sh scripts/install_tools.sh
fi
command -v docker >/dev/null
command -v colima >/dev/null
stage=colima
# A dedicated VM avoids resizing/restarting the user's existing default VM.
# Explicit flags also avoid changing the global Docker context or SSH config.
colima start --profile mlops-hw11 --cpus 4 --memory 6 --disk 12 --root-disk 8 \
    --runtime docker --vm-type vz --activate=false --ssh-config=false --mount none
unset DOCKER_CONTEXT
export DOCKER_HOST="unix://$HOME/.colima/mlops-hw11/docker.sock"
export KUBECONFIG="$project_dir/.state/kubeconfig"
printf '%s\n' 'mlops-hw11' > "$output_dir/colima-profile.txt"
stage=experiments
"$python_bin" -u scripts/cluster.py run --output "$output_dir"
stage=report
"$python_bin" scripts/build_report.py --results "$output_dir" --output "$output_dir/report.html"
cp "$output_dir/report.html" report.html
stage=verification
"$python_bin" scripts/verify_evidence.py "$output_dir" > "$output_dir/verification.log" 2>&1
stage=done
