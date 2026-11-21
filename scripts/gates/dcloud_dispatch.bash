# Dispatch a tracked, self-managed GitLab check and verify the returned SHA.

dcloud_gate_result() {
  local host="$1" endpoint="$2" pipeline="$3" commit="$4" gate="$5"
  local job_name=gate-merge evidence_name=gate-merge
  local artifact=reports/ci/gate-merge.json
  local response job standards_revision runner_image runner_digest
  if [[ -n $gate ]]; then
    job_name="project-ci-cpu-validation"
    evidence_name="$gate"
    artifact="reports/gates/$gate.json"
  fi
  response="$(GITLAB_HOST="$host" glab api "$endpoint/pipelines/$pipeline/jobs")" ||
    return 78
  job="$(jq -er --arg name "$job_name" --arg commit "$commit" '
    [.[] | select(.name == $name and .status == "success" and .commit.id == $commit)]
    | select(length == 1) | .[0].id
  ' <<<"$response")" || {
    echo "BLOCKER: no successful required DCloud job for the selected commit" >&2
    return 78
  }
  standards_revision="$(yq -er '.spec.source.revision' standards-binding.yaml)" || return 78
  runner_image="$(yq -er '.spec.image' runner.yaml)" || return 78
  runner_digest="${runner_image##*@}"
  if [[ ! $runner_digest =~ ^sha256:[a-f0-9]{64}$ ]]; then
    echo "BLOCKER: runner.yaml does not pin an approved image digest" >&2
    return 78
  fi
  response="$(GITLAB_HOST="$host" glab api "$endpoint/jobs/$job/artifacts/$artifact")" ||
    return 78
  jq -e --arg commit "$commit" --arg job "$job" --arg pipeline "$pipeline" \
    --arg name "$evidence_name" --arg revision "$standards_revision" --arg digest "$runner_digest" '
    select(.commit == $commit and .job_id == $job and .pipeline_id == $pipeline
           and .name == $name and .standards_revision == $revision
           and .runner_digest == $digest and .status == "passed"
           and .return_code == 0 and .evidence_sanitized and .warnings == 0
           and .skipped_required_tests == 0 and .cleanup.status == "passed")
    | {commit, job, job_id, pipeline_id, standards_revision, runner_digest, name, status}
  ' <<<"$response" || {
    echo "BLOCKER: DCloud job is missing successful exact-commit gate evidence" >&2
    return 78
  }
}

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
  if $no_wait || [[ $status != success ]]; then
    jq '{id,sha,ref,status,web_url}' <<<"$receipt"
    [[ $no_wait == true ]]
    return $?
  fi
  local gate_result
  gate_result="$(dcloud_gate_result "$host" "$endpoint" "$id" "$commit" "$gate")" ||
    return "$?"
  jq --argjson gate "$gate_result" '{id,sha,ref,status,web_url,gate:$gate}' <<<"$receipt"
}
