# Semantic classification key

The minimum useful response identity is:

```text
initiator
+ responder
+ method
+ target.kind
+ target.relation
+ operation kind
+ condition/resource state
+ HTTP status selector
+ Redfish MessageId policy
```

Examples:

| Exchange | Meaning |
|---|---|
| client → service, POST, subscription collection | create EventDestination |
| service → receiver, POST, subscription Destination | deliver event |
| client → service, POST, action target | invoke Redfish action |
| client → service, GET, task monitor | poll asynchronous operation |

The same numeric status can have different consequences. A `404` for an
unknown ordinary URI is not the same semantic rule as the required `404` for a
terminated event subscription. The latter also proves a lifecycle transition.

Error classification additionally includes:

```text
failure cause + HTTP status + MessageId + RelatedProperties
```
