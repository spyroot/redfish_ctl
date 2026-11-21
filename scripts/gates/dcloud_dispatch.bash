# Dispatch a tracked, self-managed GitLab check and verify the returned SHA.

dispatch_dcloud() {
  local project="$1" ref="$2" commit="$3" gate="$4" apply="$5"
  shift 5
  local host project_path endpoint variable value request receipt status id deadline
  local no_wait=false timeout_seconds=1800
  host="$(yq -er '.variables.REDFISH_CI_SERVER_HOST' .gitlab-ci.yml)" || return 78
  project_path="$(yq -er '.variables.REDFISH_CI_PROJECT_PATH' .gitlab-ci.yml)" || return 78
  if [[ ! $host =~ ^[a-zA-Z0-9.-]+$ || ${project_path##*/} != "$project" ]]; then
    echo "BLOCKER: CI authority disagrees with the project binding" >&2
    return 78
  fi
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --no-wait) no_wait=true; shift ;;
      --timeout)
        timeout_seconds="$2"
        shift 2
        ;;
      --log-format|--log-level|--run-id)
        shift 2
        ;;
      --log-file)
        echo "check.sh: DCloud dispatch reports JSON to stdout; redirect output to a file" >&2
        return 2
        ;;
    esac
  done
  if [[ ! $timeout_seconds =~ ^[1-9][0-9]{0,3}$ ]] || (( timeout_seconds > 3600 )); then
    echo "check.sh: DCloud timeout must be between 1 and 3600 seconds" >&2
    return 2
  fi
  variable=MERGE_PROFILE
  value=merge
  if [[ -n $gate ]]; then
    variable=FOCUSED_GATE
    value="$gate"
  fi
  request="$(jq -n --arg ref "$ref" --arg key "$variable" --arg value "$value" \
    '{ref:$ref,variables:[{key:$key,value:$value,variable_type:"env_var"}]}')" || return 78
  if [[ $apply != true ]]; then
    jq --arg host "$host" --arg project "$project_path" --arg commit "$commit" \
      '. + {status:"DRY_RUN",host:$host,project:$project,requestedCommit:$commit}' <<<"$request"
    return
  fi
  endpoint="projects/$(jq -nr --arg p "$project_path" '$p|@uri')"
  local encoded_ref
  encoded_ref="$(jq -nr --arg r "$ref" '$r|@uri')"
  receipt="$(GITLAB_HOST="$host" glab api "$endpoint/repository/branches/$encoded_ref")" || return 78
  if [[ $(jq -r '.commit.id' <<<"$receipt") != "$commit" ]]; then
    echo "BLOCKER: GitLab mirror ref differs from the requested local commit" >&2
    return 78
  fi
  receipt="$(GITLAB_HOST="$host" glab api --method POST "$endpoint/pipeline" --input - <<<"$request")" ||
    return 78
  if [[ $(jq -r '.sha' <<<"$receipt") != "$commit" ]]; then
    echo "BLOCKER: dispatched GitLab pipeline used a different commit" >&2
    return 78
  fi
  id="$(jq -er '.id' <<<"$receipt")" || return 78
  deadline=$((SECONDS + timeout_seconds))
  while :; do
    status="$(jq -er '.status' <<<"$receipt")" || return 78
    case "$status" in
      success|failed|canceled|skipped|manual) break ;;
    esac
    if $no_wait; then break; fi
    if ((SECONDS >= deadline)); then
      echo "BLOCKER: DCloud dispatch exceeded its result timeout" >&2
      return 78
    fi
    sleep 5
    receipt="$(GITLAB_HOST="$host" glab api "$endpoint/pipelines/$id")" || return 78
  done
  jq '{id,sha,ref,status,web_url}' <<<"$receipt"
  [[ $no_wait == true || $status == success ]]
}
