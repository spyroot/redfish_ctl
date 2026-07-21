# HTTP Status And Error Contract

This file is an agent-readable companion to the public DMTF 2026.1 Redfish
documents stored beside it. It summarizes the behavior that `redfish_ctl` must
preserve when it normalizes HTTP responses.

## Source Anchors

| Source | Local path | Contract surface |
| --- | --- | --- |
| DSP0266 Redfish Specification 1.24.0 | `../protocol/DSP0266_1.24.0.pdf` | Protocol responses, modification responses, task handling, query errors, and error response envelope. |
| DSP0268 Redfish Data Model Specification 2026.1 | `../data-model/DSP0268_2026.1.pdf` | `@Message.ExtendedInfo`, `@Redfish.AllowableValues`, schema-bound resource properties. |
| DSP8010 Redfish Schema Bundle 2026.1 | `../schemas/DSP8010_2026.1.zip` | JSON schema, CSDL/XML, YAML/OpenAPI schema files. |
| DSP8011 Redfish Standard Registries Bundle 2026.1 | `../registries/DSP8011_2026.1.zip` | Base, Telemetry, Event, Task, and OEM-adjacent message registry shapes. |

## Response Classes

| HTTP status | Redfish handling rule |
| --- | --- |
| `200 OK` | Success with a response body, or action success with body-level messages. Preserve any `@Message.ExtendedInfo` present. |
| `201 Created` | Resource creation succeeded. Preserve representation and `Location` when present. |
| `202 Accepted` | Operation is asynchronous. Preserve `Location`, task monitor URI, task resource data, and `Retry-After` when present. Do not report final success until task status is read back. |
| `204 No Content` | Operation succeeded with no response body. Do not fabricate a JSON object beyond the command result envelope. |
| `304 Not Modified` | Conditional GET reports no representation change. Preserve the cache/ETag context. |
| `400 Bad Request` | Invalid payload, invalid query value, non-updatable property, or other client request error. Parse the Redfish error body. |
| `401 Unauthorized` | Authentication failure. Terminal unless a documented credential refresh path exists. Parse the Redfish error body when present. |
| `403 Forbidden` | Authorization failure. Terminal. Parse the Redfish error body when present. |
| `404 Not Found` | Missing target resource, deleted task monitor, or absent operation target. Parse the Redfish error body when present. |
| `405 Method Not Allowed` | Unsupported method on the target resource. Preserve `Allow` header and Redfish error body when present. |
| `409 Conflict` | State conflict. Preserve the Redfish error body and any resolution message. |
| `412 Precondition Failed` | Conditional request or ETag precondition failed. Preserve precondition context and Redfish error body. |
| `415 Unsupported Media Type` | Request media type or payload format not supported. Parse the Redfish error body. |
| `428 Precondition Required` | Service requires a conditional request. Preserve the required-condition context. |
| `431 Request Header Fields Too Large` | Header envelope rejected. Preserve status and any Redfish error body. |
| `500 Internal Server Error` | Service failure. Parse the Redfish error body and preserve registry details. |
| `501 Not Implemented` | Unsupported operation, feature, or query behavior. This is still structured Redfish data when an error envelope is returned. |
| `503 Service Unavailable` | Service unavailable or temporarily unable to process the request. Preserve retry and availability hints. |

## Error Envelope

Every Redfish error response with an `error` object is normalized with this
shape:

```json
{
  "status_code": 501,
  "target": "/redfish/v1/Managers/1/VirtualMedia",
  "error": {
    "code": "Base.1.18.GeneralError",
    "message": "A Redfish service error occurred.",
    "@Message.ExtendedInfo": [
      {
        "MessageId": "Base.1.18.ActionNotSupported",
        "Message": "The action is not supported.",
        "MessageArgs": ["VirtualMedia"],
        "Resolution": "Use a supported operation."
      }
    ]
  }
}
```

Rules:

- `error.code` and `MessageId` are not interchangeable; preserve both.
- `@Message.ExtendedInfo` can be present at the top-level error object or as a
  property annotation such as `ResetType@Message.ExtendedInfo`.
- `Message` can be absent or `null`; `MessageId`, `MessageArgs`, severity, and
  resolution still carry protocol data.
- If registry expansion is available, use DSP8011 by registry prefix and
  version. Do not assume one latest Base registry.
- If the body is not recognized, keep status, target, headers, raw body, and
  parse exception in an unclassified Redfish error object.

## Operation Rules

Modification operations use the protocol response class:

- create: `201`, `202`, or `204` depending on body and async behavior;
- update: `200`, `202`, or `204`;
- action: `200`, `201`, `202`, or `204`;
- error: client `4xx` or service `5xx` with a Redfish error body when the
  service can return one.

Query handling is also protocol data:

- invalid query parameter values produce a client error;
- unsupported Redfish query behavior can produce `400` or `501` depending on
  the protocol case;
- command code must preserve the returned `@Message.ExtendedInfo` rather than
  replacing it with a local unsupported string.

## Agent Rules

- Do not create a hand-built `{"error": "...", "status_code": ...}` result for
  an HTTP failure. Call the Redfish parser and render its normalized object.
- Do not route JSON or YAML through `str(exception)`.
- Do not hide `--insecure` SSL suppression behavior inside output formatting.
  SSL warning suppression belongs to transport setup and must be tested there.
- Do not report asynchronous mutation success from the initial `202` alone.
  Read the task state or return a task-pending result with the monitor URI.
