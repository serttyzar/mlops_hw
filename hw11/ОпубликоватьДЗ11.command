#!/bin/bash
# User-initiated publication of already reviewed commits. No staging or commits.
set -Eeuo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_dir"
mkdir -p hw11/results
log_file="$repo_dir/hw11/results/publish.log"
status_file="$repo_dir/hw11/results/publish-status.json"
if [ "${1:-}" != '--check-only' ]; then
    exec > >(tee -a "$log_file") 2>&1
fi
path_list=''
finish() {
    code=$?
    trap - EXIT
    if [ -n "$path_list" ]; then rm -f "$path_list"; fi
    if [ "$code" -ne 0 ]; then
        printf '{"state":"failed","exit_code":%s}\n' "$code" > "$status_file"
        printf '\nПубликация не подтверждена. Журнал: %s\n' "$log_file"
    fi
    if [ -t 0 ]; then read -r -p 'Нажмите Enter, чтобы закрыть окно. ' unused || true; fi
    exit "$code"
}
trap finish EXIT
printf '\nПубликация ДЗ 11, %s\n' "$(date -u +%FT%TZ)"
# Do not accidentally publish a different repository or unrelated commits.
test "$(git remote get-url origin)" = 'https://github.com/serttyzar/mlops_hw.git'
test "$(git branch --show-current)" = main
git merge-base --is-ancestor a490e568be55cd3bc76265b85e22ede3ac2ce25b HEAD
# NUL separators preserve UTF-8, spaces and embedded newlines verbatim.
# A file makes a failed git diff fatal instead of hiding it in a subshell.
path_list=$(mktemp)
git diff --name-only -z a490e568be55cd3bc76265b85e22ede3ac2ce25b^ HEAD > "$path_list"
reviewed=0
while IFS= read -r -d '' changed_path; do
    reviewed=$((reviewed + 1))
    case "$changed_path" in hw11/*|README.md|.dockerignore) ;; *)
        printf 'Есть дополнительные изменения истории вне ДЗ 11: %s\n' "$changed_path" >&2
        exit 2;;
    esac
done < "$path_list"
rm -f "$path_list"
path_list=''
printf 'Проверены пути файлов: %s. Посторонних изменений истории нет.\n' "$reviewed"
if [ "${1:-}" = '--check-only' ]; then
    printf 'Проверка завершена без обращения к GitHub и без публикации.\n'
    exit 0
fi
# Normal fast-forward push; never force. Use the existing credential helper.
export GIT_TERMINAL_PROMPT=0
git push origin main
local_sha=$(git rev-parse refs/heads/main)
remote_line=$(git ls-remote --exit-code origin refs/heads/main)
remote_sha=${remote_line%%[[:space:]]*}
test "$local_sha" = "$remote_sha"
printf '{"state":"published","head":"%s","artifact_commit":"a490e568be55cd3bc76265b85e22ede3ac2ce25b","remote_verified":true}\n' "$remote_sha" > "$status_file"
printf '\nПубликация подтверждена. Коммит сдачи:\nhttps://github.com/serttyzar/mlops_hw/commit/a490e568be55cd3bc76265b85e22ede3ac2ce25b\n'
printf 'Журнал публикации: %s\n' "$log_file"
