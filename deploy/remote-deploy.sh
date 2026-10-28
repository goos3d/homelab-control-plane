#!/usr/bin/env bash
set -Eeuo pipefail

bundle=${1:?Usage: remote-deploy.sh BUNDLE EXPECTED_COMMIT}
expected=${2:?Usage: remote-deploy.sh BUNDLE EXPECTED_COMMIT}
repo=${3:?Usage: remote-deploy.sh BUNDLE EXPECTED_COMMIT REPO BRANCH SERVICE HEALTH_URL STATE_DIR USER}
branch=${4:?Usage: remote-deploy.sh BUNDLE EXPECTED_COMMIT REPO BRANCH SERVICE HEALTH_URL STATE_DIR USER}
service=${5:?Usage: remote-deploy.sh BUNDLE EXPECTED_COMMIT REPO BRANCH SERVICE HEALTH_URL STATE_DIR USER}
health_url=${6:?Usage: remote-deploy.sh BUNDLE EXPECTED_COMMIT REPO BRANCH SERVICE HEALTH_URL STATE_DIR USER}
state_relative=${7:?Usage: remote-deploy.sh BUNDLE EXPECTED_COMMIT REPO BRANCH SERVICE HEALTH_URL STATE_DIR USER}
service_owner=${8:?Usage: remote-deploy.sh BUNDLE EXPECTED_COMMIT REPO BRANCH SERVICE HEALTH_URL STATE_DIR USER}

if [[ ! "$repo" =~ ^/[A-Za-z0-9._/-]+$ || "$repo" =~ (^|/)\.\.?(/|$) ]]; then
    echo 'Repository path must be an absolute path without traversal components.' >&2
    exit 1
fi
if [[ ! "$branch" =~ ^[A-Za-z0-9._/-]+$ ]] || ! git check-ref-format --branch "$branch" >/dev/null; then
    echo "Invalid deployment branch: $branch" >&2
    exit 1
fi
if [[ ! "$service" =~ ^[A-Za-z0-9_.@-]+$ ]]; then
    echo 'Invalid systemd service name.' >&2
    exit 1
fi
if [[ ! "$health_url" =~ ^http://(127\.0\.0\.1|localhost):[0-9]{1,5}/health$ ]]; then
    echo 'Health URL must target the local Control Plane health endpoint.' >&2
    exit 1
fi
if [[ "$state_relative" == /* ||
      ! "$state_relative" =~ ^[A-Za-z0-9._/-]+$ ||
      "$state_relative" =~ (^|/)\.\.?(/|$) ]]; then
    echo 'Deployment state directory must be a safe path relative to the remote home.' >&2
    exit 1
fi
if [[ ! "$service_owner" =~ ^[A-Za-z_][A-Za-z0-9_.-]*$ || "$(id -un)" != "$service_owner" ]]; then
    echo 'SSH user must match the configured service account.' >&2
    exit 1
fi

state_dir="$HOME/$state_relative"
history_file="$state_dir/history.log"

mkdir -p "$state_dir"
exec 9>"$state_dir/deploy.lock"
if ! flock -n 9; then
    echo 'Another Control Plane deployment is already running.' >&2
    exit 1
fi

if [[ ! "$expected" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Expected commit is not a full Git SHA: $expected" >&2
    exit 1
fi

if [[ ! -f "$bundle" ]]; then
    echo "Deployment bundle does not exist: $bundle" >&2
    exit 1
fi

cd "$repo"

current_branch=$(git branch --show-current)
if [[ "$current_branch" != "$branch" ]]; then
    echo "Production must be on branch '$branch'; found '$current_branch'." >&2
    exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
    echo 'Production has tracked changes; deployment stopped.' >&2
    git status --short
    exit 1
fi

repo_env="$repo/.env"
service_env="$HOME/.config/control-plane/control-plane.env"
for secret in "$repo_env" "$service_env"; do
    if [[ ! -f "$secret" ]]; then
        echo "Required secret file is missing: $secret" >&2
        exit 1
    fi
    mode=$(stat -c '%a' "$secret")
    owner=$(stat -c '%U' "$secret")
    if [[ "$mode" != 600 || "$owner" != "$service_owner" ]]; then
        echo "Unsafe secret metadata for $secret: owner=$owner mode=$mode" >&2
        exit 1
    fi
done

if git ls-files --error-unmatch .env >/dev/null 2>&1; then
    echo 'Repository .env is tracked; deployment stopped.' >&2
    exit 1
fi
if ! git check-ignore -q .env; then
    echo 'Repository .env is not ignored; deployment stopped.' >&2
    exit 1
fi

if ! systemctl --user is-active --quiet "$service"; then
    echo "User service is not active before deployment: $service" >&2
    exit 1
fi

git bundle verify "$bundle"
git fetch --no-tags "$bundle" \
    "refs/heads/$branch:refs/remotes/deploy/$branch"

target=$(git rev-parse "refs/remotes/deploy/$branch")
current=$(git rev-parse HEAD)
if [[ "$target" != "$expected" ]]; then
    echo "Bundle commit $target does not match approved commit $expected." >&2
    exit 1
fi

if ! git merge-base --is-ancestor "$current" "$target"; then
    echo "Deployment is not a fast-forward from $current to $target." >&2
    exit 1
fi

if ! git diff --quiet "$current" "$target" -- requirements.txt; then
    echo 'requirements.txt changed; use the reviewed dependency deployment procedure.' >&2
    exit 1
fi

if ! git diff --quiet "$current" "$target" -- deploy/control-plane-api.service; then
    echo 'The service definition changed; install and review it separately before deployment.' >&2
    exit 1
fi

deployed=0
rollback() {
    status=$?
    if [[ "$deployed" == 1 ]]; then
        echo "Deployment failed; returning production to $current." >&2
        git reset --hard "$current"
        systemctl --user restart "$service"
        if curl --fail --silent --show-error --max-time 5 "$health_url" >/dev/null; then
            echo "Rollback health check passed at $current." >&2
        else
            echo 'WARNING: rollback completed but the health check is still failing.' >&2
        fi
        printf '%s status=rolled-back from=%s attempted=%s\n' \
            "$(date --iso-8601=seconds)" "$current" "$target" >>"$history_file"
    fi
    exit "$status"
}
trap rollback ERR

printf '%s status=starting from=%s target=%s\n' \
    "$(date --iso-8601=seconds)" "$current" "$target" >>"$history_file"

git merge --ff-only "$target"
deployed=1
systemctl --user restart "$service"

healthy=0
for _ in $(seq 1 20); do
    if systemctl --user is-active --quiet "$service" && \
        curl --fail --silent --show-error --max-time 5 "$health_url" >/dev/null; then
        healthy=1
        break
    fi
    sleep 1
done

if [[ "$healthy" != 1 ]]; then
    echo 'Service did not become healthy within 20 seconds.' >&2
    false
fi

deployed=0
trap - ERR
printf '%s status=success from=%s target=%s\n' \
    "$(date --iso-8601=seconds)" "$current" "$target" >>"$history_file"

echo "Production is healthy at $target."
curl --fail --silent --show-error --max-time 5 "$health_url"
echo
