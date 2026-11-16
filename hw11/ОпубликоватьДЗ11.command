#!/bin/bash
# User-initiated publication of already reviewed commits. No staging or commits.
set -Eeuo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_dir"
mkdir -p hw11/results
log_file="$repo_dir/hw11/results/publish.log"
status_file="$repo_dir/hw11/results/publish-status.json"
exec > >(tee -a "$log_file") 2>&1
finish() {
    code=$?
    trap - EXIT
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
while IFS= read -r changed_path; do
    case "$changed_path" in hw11/*|README.md|.dockerignore) ;; *)
        printf 'Есть дополнительные изменения истории вне ДЗ 11: %s\n' "$changed_path" >&2
        exit 2;;
    esac
done < <(git diff --name-only a490e568be55cd3bc76265b85e22ede3ac2ce25b^ HEAD)
# Normal fast-forward push; never force. Use the existing credential helper.
export GIT_TERMINAL_PROMPT=0
git push origin main
local_sha=$(git rev-parse refs/heads/main)
remote_line=$(git ls-remote --exit-code origin refs/heads/main)
remote_sha=${remote_line%%[[:space:]]*}
test "$local_sha" = "$remote_sha"
printf '{"state":"published","head":"%s","artifact_commit":"a490e568be55cd3bc76265b85e22ede3ac2ce25b","remote_verified":true}\n' "$remote_sha" > "$status_file"
printf '\nПубликация подтверждена. Коммит сдачи:\nhttps://github.com/serttyzar/mlops_hw/commit/a490e568be55cd3bc76265b85e22ede3ac2ce25b\n'
printf 'Вернитесь в чат и напишите «опубликовано».\n'
