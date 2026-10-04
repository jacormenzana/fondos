#!/bin/bash
# wsl_02_docker_cleanup.sh — reclaim Docker garbage safely (run weekly).
#   bash wsl_02_docker_cleanup.sh [--apply]
# Reports docker disk usage and container json-log sizes. --apply removes ONLY: stopped containers older
# than DOCKER_KEEP_H hours, dangling images, build cache older than DOCKER_KEEP_H, unused networks.
# It NEVER prunes volumes (live Postgres data lives in bind mounts/volumes) and never touches a running
# container or an image a container uses. Pass --truncate-logs to also empty json logs > DOCKER_LOG_MB.
# Env: DOCKER_KEEP_H=168 DOCKER_LOG_MB=200
set -uo pipefail
. "$(dirname "$0")/../lib/common.sh"; maint_args "$@"; maint_start wsl_02_docker_cleanup
KEEP=${DOCKER_KEEP_H:-168}; LOGMB=${DOCKER_LOG_MB:-200}; TRUNC=0; [[ " ${ARGS[*]:-} " == *" --truncate-logs "* ]] && TRUNC=1
docker info >/dev/null 2>&1 || { crit "docker daemon not reachable"; maint_end; }

docker system df
info "stopped containers: $(docker ps -aq -f status=exited -f status=created -f status=dead | wc -l), dangling images: $(docker images -qf dangling=true | wc -l)"

while read -r id n; do
  f=$(docker inspect -f '{{.LogPath}}' "$id" 2>/dev/null); [ -n "$f" ] && [ -f "$f" ] || continue
  mb=$(( $(stat -c %s "$f")/1048576 ))
  if [ "$mb" -gt "$LOGMB" ]; then
    warn "container $n log = ${mb} MB (> ${LOGMB}); set logging max-size/max-file in compose to stop growth"
    if [ $TRUNC -eq 1 ] && need_apply "truncate $n log"; then act "truncate $n log"; truncate -s 0 "$f"; fi
  else ok "container $n log ${mb} MB"; fi
done < <(docker ps -a --format '{{.ID}} {{.Names}}')

if need_apply "docker container/image/builder/network prune (older than ${KEEP}h)"; then
  act "container prune";  docker container prune -f --filter "until=${KEEP}h"
  act "image prune (dangling)"; docker image prune -f
  act "builder prune";    docker builder prune -f --filter "until=${KEEP}h"
  act "network prune";    docker network prune -f
  docker system df
fi
maint_end
