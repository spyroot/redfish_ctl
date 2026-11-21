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

dcloud_verify_smoke_result() {
  local host="$1" endpoint="$2" gate_result="$3"
  local job response
  job="$(jq -er '.job_id' <<<"$gate_result")" || return 78
  response="$(GITLAB_HOST="$host" glab api "$endpoint/jobs/$job/artifacts/reports/smoke/gate-merge.json")" ||
    return 78
  jq -e --argjson gate "$gate_result" '
    select(.kind == "smoke" and .name == $gate.name and .commit == $gate.commit
           and .job_id == $gate.job_id and .pipeline_id == $gate.pipeline_id
           and .standards_revision == $gate.standards_revision and .runner_digest == $gate.runner_digest
           and .status == "passed" and .return_code == 0 and .evidence_sanitized
           and .warnings == 0 and .skipped_required_tests == 0 and .cleanup.status == "passed"
           and (.checks|length > 0) and all(.checks[]; .status == "passed"))
  ' <<<"$response" >/dev/null || {
    echo "BLOCKER: DCloud job is missing successful exact-commit smoke evidence" >&2
    return 78
  }
}

dcloud_post_github_status() {
  local repo="$1" commit="$2" state="$3" target_url="$4" context="$5"
  local created status_id response attempt
  created="$(gh api --hostname github.com --method POST "repos/$repo/statuses/$commit" \
    -f "state=$state" -f "context=$context" -f "target_url=$target_url" \
    -f "description=DCloud gate-merge: $state")" || return 78
  status_id="$(jq -er '.id | tostring' <<<"$created")" || return 78
  for attempt in 1 2 3; do
    response="$(gh api --hostname github.com "repos/$repo/commits/$commit/status")" || return 78
    if jq -e --arg repo "$repo" --arg sha "$commit" --arg state "$state" \
      --arg context "$context" --arg target "$target_url" --arg status_id "$status_id" '
        .sha == $sha and .repository.full_name == $repo and any(.statuses[];
          (.id|tostring) == $status_id and .context == $context
          and .state == $state and .target_url == $target)
      ' <<<"$response" >/dev/null; then
      return 0
    fi
    if ((attempt < 3)); then sleep 1; fi
  done
  echo "BLOCKER: the exact DCloud status could not be read back from GitHub" >&2
  return 78
}

dcloud_wait_pipeline() {
  local host="$1" endpoint="$2" id="$3" commit="$4" timeout_seconds="$5"
  local deadline=$((SECONDS + timeout_seconds))
  local receipt status
  while ((SECONDS < deadline)); do
    receipt="$(GITLAB_HOST="$host" glab api "$endpoint/pipelines/$id")" || return 78
    status="$(jq -er --arg sha "$commit" 'select(.sha == $sha).status' <<<"$receipt")" || return 78
    case "$status" in
      success|failed|canceled|skipped|manual) printf '%s\n' "$receipt"; return 0 ;;
    esac
    sleep 5
  done
  echo "BLOCKER: DCloud dispatch exceeded its result timeout" >&2
  return 78
}

dispatch_dcloud() {
  local project="$1" ref="$2" commit="$3" gate="$4" apply="$5"
  shift 5
  local host project_path endpoint variable value request receipt status id
  local no_wait=false timeout_seconds=1800
  host="$(yq -er '.variables.REDFISH_CI_SERVER_HOST' .gitlab-ci.yml)" || return 78
  project_path="$(yq -er '.variables.REDFISH_CI_PROJECT_PATH' .gitlab-ci.yml)" || return 78
  if [[ ! $host =~ ^[a-zA-Z0-9.-]+$ || ! $project_path =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ||
        ${project_path##*/} != "$project" ]]; then
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
  local reflect=false remote url repo_url context
  url="https://$host/$project_path/-/pipelines?sha=$commit"
  if [[ -z $gate && $no_wait == false ]]; then
    remote="$(git remote get-url origin)" || return 78
    repo_url="https://github.com/$project_path"
    if [[ ${remote%.git} != "$repo_url" ]]; then
      echo "BLOCKER: GitHub status target disagrees with origin" >&2
      return 78
    fi
    context="$(yq -er '.spec.ci.requiredContext' inventory/ci/go-no-go.yaml)" || return 78
    if [[ -z $context || $context == null ]]; then
      echo "BLOCKER: the merge inventory lacks a required GitHub status context" >&2
      return 78
    fi
    dcloud_post_github_status "$project_path" "$commit" pending "$url" "$context" || return 78
    reflect=true
  fi
  if ! receipt="$(GITLAB_HOST="$host" glab api --method POST "$endpoint/pipeline" --input - <<<"$request")" ||
     ! id="$(jq -er --arg sha "$commit" 'select(.sha == $sha and (.id|type) == "number").id' <<<"$receipt")"; then
    if $reflect; then
      dcloud_post_github_status "$project_path" "$commit" error "$url" "$context" || return 78
    fi
    echo "BLOCKER: GitLab did not create the selected exact-commit pipeline" >&2
    return 78
  fi
  if $no_wait; then
    jq '{id,sha,ref,status,web_url}' <<<"$receipt"
    return
  fi
  url="https://$host/$project_path/-/pipelines/$id"
  if ! receipt="$(dcloud_wait_pipeline "$host" "$endpoint" "$id" "$commit" "$timeout_seconds")"; then
    if $reflect; then
      dcloud_post_github_status "$project_path" "$commit" error "$url" "$context" || return 78
    fi
    return 78
  fi
  status="$(jq -er '.status' <<<"$receipt")" || return 78
  local gate_result
  if [[ $status != success ]] ||
     ! gate_result="$(dcloud_gate_result "$host" "$endpoint" "$id" "$commit" "$gate")" ||
     { $reflect && ! dcloud_verify_smoke_result "$host" "$endpoint" "$gate_result"; }; then
    if $reflect; then
      dcloud_post_github_status "$project_path" "$commit" failure "$url" "$context" || return 78
    fi
    jq '{id,sha,ref,status:"failed",pipeline_status:.status,web_url}' <<<"$receipt"
    return 1
  fi
  if $reflect; then
    dcloud_post_github_status "$project_path" "$commit" success "$url" "$context" || return 78
  fi
  jq --argjson gate "$gate_result" '{id,sha,ref,status,web_url,gate:$gate}' <<<"$receipt"
}
