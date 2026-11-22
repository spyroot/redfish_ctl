# Effective Redfish HTTP semantic matrix

Generated from the canonical YAML contracts. Do not edit manually.

| ID | Type | Method | Accepted status | Preferred emit | Source | Summary |
|---|---|---|---|---|---|---|
| `http.status.200` | status_catalog | `*` | `200` | `` | 8.3/Table14 | completed_with_representation |
| `http.status.201` | status_catalog | `*` | `201` | `` | 8.3/Table14 | create_completed |
| `http.status.202` | status_catalog | `*` | `202` | `` | 8.3/Table14 | accepted_not_complete |
| `http.status.204` | status_catalog | `*` | `204` | `` | 8.3/Table14 | completed_without_representation |
| `http.status.301` | status_catalog | `*` | `301` | `` | 8.3/Table14 | resource_permanently_moved |
| `http.status.302` | status_catalog | `*` | `302` | `` | 8.3/Table14 | resource_temporarily_moved |
| `http.status.304` | status_catalog | `*` | `304` | `` | 8.3/Table14 | conditional_get_unchanged |
| `http.status.400` | status_catalog | `*` | `400` | `` | 8.3/Table14 | invalid_or_missing_request_information |
| `http.status.401` | status_catalog | `*` | `401` | `` | 8.3/Table14 | missing_or_invalid_authentication |
| `http.status.403` | status_catalog | `*` | `403` | `` | 8.3/Table14 | authenticated_but_not_authorized |
| `http.status.404` | status_catalog | `*` | `404` | `` | 8.3/Table14 | resource_uri_does_not_exist_or_is_hidden |
| `http.status.405` | status_catalog | `*` | `405` | `` | 8.3/Table14 | method_not_supported_for_target_uri |
| `http.status.406` | status_catalog | `*` | `406` | `` | 8.3/Table14 | requested_representation_not_available |
| `http.status.409` | status_catalog | `*` | `409` | `` | 8.3/Table14 | create_or_update_conflicts_with_current_resource_state |
| `http.status.410` | status_catalog | `*` | `410` | `` | 8.3/Table14 | resource_permanently_unavailable |
| `http.status.411` | status_catalog | `*` | `411` | `` | 8.3/Table14 | content_length_required |
| `http.status.412` | status_catalog | `*` | `412` | `` | 8.3/Table14 | supplied_precondition_failed |
| `http.status.413` | status_catalog | `*` | `413` | `` | 8.3/Table14 | request_or_multipart_part_exceeds_supported_size |
| `http.status.415` | status_catalog | `*` | `415` | `` | 8.3/Table14 | unsupported_request_content_type |
| `http.status.428` | status_catalog | `*` | `428` | `` | 8.3/Table14 | required_precondition_missing |
| `http.status.431` | status_catalog | `*` | `431` | `` | 8.3/Table14 | request_headers_too_large |
| `http.status.500` | status_catalog | `*` | `500` | `` | 8.3/Table14 | unexpected_service_failure |
| `http.status.501` | status_catalog | `*` | `501` | `` | 8.3/Table14 | functionality_or_method_not_supported_anywhere |
| `http.status.503` | status_catalog | `*` | `503` | `` | 8.3/Table14 | temporary_overload_initialization_or_maintenance |
| `http.status.507` | status_catalog | `*` | `507` | `` | 8.3/Table14 | service_cannot_construct_response_due_to_response_size |
| `global.error.extended-response` | payload_contract | `` | `` | `` | 8.3,8.6 | extended-error-shape |
| `global.error.no-privileged-auth-details` | security_invariant | `` | `` | `` | 8.3 | authentication-error-information-boundary |
| `method.post-create.success` | http_exchange | `"POST"` | `one_of:201,202,204` | `` | 7.5.2,7.9,8.3 | generic-post-create-success |
| `method.update-delete.success` | http_exchange | `["PATCH","PUT","DELETE"]` | `one_of:200,202,204` | `` | 7.5.2 | generic-patch-put-delete-success |
| `method.modification.error-is-atomic` | state_invariant | `` | `` | `` | 7.5.3 | modification-error-does-not-change-resource |
| `event.subscription.create.completed` | http_exchange | `"POST"` | `exact:201` | `201` | 12.1.2,8.3 | event-subscription-create-completed |
| `event.subscription.create.body-representation` | payload_contract | `` | `` | `` | 12.1.2 | event-subscription-create-response-body |
| `event.subscription.create.accepted-async` | http_exchange | `"POST"` | `exact:202` | `202` | 7.5.2,12.2 | inherited-asynchronous-subscription-create |
| `event.subscription.create.unsupported-parameter` | http_exchange | `"POST"` | `exact:400` | `400` | 12.1.2,8.3,8.6 | unsupported-subscription-parameter |
| `event.subscription.create.conflicting-body` | http_exchange | `"POST"` | `derived:4xx` | `` | 12.1.2,8.3,8.6 | conflicting-subscription-request-body |
| `event.push.supported-for-event-capable-resources` | capability | `` | `` | `` | 12.1.2 | push-eventing-supported-for-event-capable-resources |
| `event.push.requires-active-subscription` | prohibition | `"POST"` | `` | `` | 12.1.2 | no-push-without-subscription |
| `event.push.delivery-acknowledgement` | http_exchange | `"POST"` | `class:2xx` | `204` | 12.1.2 | event-receiver-success-acknowledgement |
| `event.push.payload-limit` | payload_constraint | `"POST"` | `` | `` | 12.1.2 | event-payload-size-limit |
| `event.push.metric-report-payload-exemption` | payload_constraint | `` | `` | `` | 12.1.2 | metric-report-payload-limit-exemption |
| `event.subscription.persistent-across-restart` | lifecycle_invariant | `` | `` | `` | 12.1.2 | subscription-persists-across-service-restart |
| `event.subscription.no-historical-replay` | lifecycle_invariant | `` | `` | `` | 12.1.2 | no-retroactive-events-or-history-retention |
| `event.subscription.delete` | http_exchange | `"DELETE"` | `one_of:200,202,204` | `204` | 12.1.2,7.5.2 | unsubscribe-by-deleting-subscription |
| `event.subscription.request-after-termination` | http_exchange | `"*"` | `exact:404` | `404` | 12.1.2 | terminated-subscription-subsequent-request |
| `event.subscription.service-termination-after-errors` | lifecycle_transition | `` | `` | `` | 12.1.2 | service-may-terminate-after-delivery-errors |
| `event.subscription.termination-last-event` | optional_event | `` | `` | `` | 12.1.2 | subscription-terminated-message-as-last-event |
| `task.operation.accepted` | http_exchange | `` | `exact:202` | `202` | 8.3,12.2 | asynchronous-operation-accepted |
| `task.monitor.pending` | http_exchange | `"GET"` | `exact:202` | `202` | 12.2 | task-monitor-pending-poll |
| `task.monitor.completed` | http_exchange | `"GET"` | `` | `` | 12.2 | task-monitor-completed-replays-initial-response |
| `task.monitor.expired` | http_exchange | `"GET"` | `one_of:404,410` | `` | 12.2 | completed-task-monitor-no-longer-retained |
