#!/bin/bash
# Stop only the homework VM. Artifacts and result files are preserved.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
colima stop --profile mlops-hw11
code=$?
printf '\nПрофиль mlops-hw11 остановлен; результаты сохранены в проекте.\n'
read -r -p 'Нажмите Enter, чтобы закрыть окно. ' unused
exit "$code"
