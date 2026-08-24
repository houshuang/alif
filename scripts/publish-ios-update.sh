#!/usr/bin/env bash

set -euo pipefail
umask 077

readonly ALIF_HOST="alifstian.duckdns.org"
readonly DEFAULT_CAPABILITY_FILE="${HOME}/.config/alif/api-capability"

usage() {
  cat >&2 <<'EOF'
Usage:
  scripts/publish-ios-update.sh --preflight-only
  scripts/publish-ios-update.sh "update message"

Publishes the iOS preview OTA with the private HTTPS API capability embedded.
The capability is read from ~/.config/alif/api-capability and is never printed.
Publishing is allowed only from a clean, up-to-date main branch.
EOF
}

mode="publish"
message=""
case "${1:-}" in
  --preflight-only)
    [[ $# -eq 1 ]] || { usage; exit 2; }
    mode="preflight"
    ;;
  -h|--help)
    usage
    exit 0
    ;;
  "")
    usage
    exit 2
    ;;
  *)
    [[ $# -eq 1 ]] || { usage; exit 2; }
    message=$1
    ;;
esac

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "${script_dir}/.." && pwd)
frontend_dir="${repo_root}/frontend"
capability_file="${ALIF_API_CAPABILITY_FILE:-${DEFAULT_CAPABILITY_FILE}}"

for command_name in git node npx eas curl; do
  command -v "${command_name}" >/dev/null 2>&1 || {
    echo "Missing required command: ${command_name}" >&2
    exit 1
  }
done

[[ -r "${capability_file}" ]] || {
  echo "Private Alif API capability file is missing or unreadable." >&2
  exit 1
}

capability_segment=$(tr -d '\r\n' < "${capability_file}")
if [[ ! "${capability_segment}" =~ ^alif-private-[a-f0-9]{64}$ ]]; then
  echo "Private Alif API capability has an unexpected format." >&2
  exit 1
fi

api_url="https://${ALIF_HOST}/${capability_segment}"

if [[ "${mode}" == "publish" ]]; then
  current_branch=$(git -C "${repo_root}" branch --show-current)
  [[ "${current_branch}" == "main" ]] || {
    echo "Refusing to publish from '${current_branch:-detached HEAD}'; use main." >&2
    exit 1
  }

  [[ -z "$(git -C "${repo_root}" status --porcelain)" ]] || {
    echo "Refusing to publish from a dirty checkout." >&2
    exit 1
  }

  git -C "${repo_root}" fetch --quiet origin main
  local_head=$(git -C "${repo_root}" rev-parse HEAD)
  remote_head=$(git -C "${repo_root}" rev-parse origin/main)
  [[ "${local_head}" == "${remote_head}" ]] || {
    echo "Refusing to publish: local main is not exactly origin/main." >&2
    exit 1
  }
fi

config_json=$(cd "${frontend_dir}" && ALIF_API_URL="${api_url}" npx expo config --json)
runtime_version=$(printf '%s' "${config_json}" | ALIF_EXPECTED_URL="${api_url}" node -e '
  let input = "";
  process.stdin.on("data", chunk => { input += chunk; });
  process.stdin.on("end", () => {
    const config = JSON.parse(input);
    const actual = config?.extra?.apiUrl;
    const expected = process.env.ALIF_EXPECTED_URL;
    if (actual !== expected || actual.includes("alif-api-not-configured")) {
      console.error("Expo config did not resolve to the expected private API URL.");
      process.exit(1);
    }
    if (typeof config?.version !== "string" || config.version.length === 0) {
      console.error("Expo config did not resolve an app version.");
      process.exit(1);
    }
    process.stdout.write(config.version);
  });
')

echo "Secure Alif API preflight passed."
[[ "${mode}" == "publish" ]] || exit 0

publish_json_file=$(mktemp "${TMPDIR:-/tmp}/alif-eas-update.XXXXXX")
cleanup() {
  rm -f -- "${publish_json_file}"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

(
  cd "${frontend_dir}"
  ALIF_API_URL="${api_url}" CI=1 eas update \
    --channel preview \
    --platform ios \
    --message "${message}" \
    --json
) > "${publish_json_file}"

IFS=$'\t' read -r update_id group_id manifest_permalink < <(
  node -e '
    const fs = require("fs");
    const updates = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
    const update = updates.find(item => item.platform === "ios");
    if (!update?.id || !update?.group || !update?.manifestPermalink) {
      console.error("EAS did not return complete iOS update metadata.");
      process.exit(1);
    }
    process.stdout.write(
      update.id + "\t" + update.group + "\t" + update.manifestPermalink + "\n"
    );
  ' "${publish_json_file}"
)

curl --fail --silent --show-error \
  -H 'expo-platform: ios' \
  -H "expo-runtime-version: ${runtime_version}" \
  -H 'expo-protocol-version: 1' \
  "${manifest_permalink}" \
| ALIF_EXPECTED_URL="${api_url}" node -e '
  const chunks = [];
  process.stdin.on("data", chunk => chunks.push(chunk));
  process.stdin.on("end", () => {
    const body = Buffer.concat(chunks).toString("utf8");
    const expected = process.env.ALIF_EXPECTED_URL;
    if (!body.includes(expected) || body.includes("alif-api-not-configured")) {
      console.error("Published EAS manifest failed the private API URL check.");
      process.exit(1);
    }
  });
'

echo "Published and verified secure iOS update ${update_id}."
echo "Update group: ${group_id}"
echo "Dashboard: https://expo.dev/accounts/houshuang/projects/alif/updates/${group_id}"
