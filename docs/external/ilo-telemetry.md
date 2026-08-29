# HPE iLO telemetry: metric reports and subscriptions

HPE iLO exposes the DMTF TelemetryService with an HPE-specific eligibility
rule and, between iLO generations, two different subscription payload forms.
This page records the vendor facts `redfish_ctl` follows when listing,
subscribing to, and scheduling iLO metric reports. Source: the HPE server
management portal, "iLO telemetry service"
(`https://servermanagementportal.ext.hpe.com/docs/redfishservices/ilos/supplementdocuments/ilotelemetryservice`),
as of iLO 5 2.96 and iLO 6 1.51.

## Eligible metric reports

Reports eligible for subscription are the members of the Metric Report
Definition Collection (`/redfish/v1/TelemetryService/MetricReportDefinitions/`)
whose URI does **not** contain the string `Custom`. On iLO 5 2.96 / iLO 6 1.51
that list is:

```text
/redfish/v1/TelemetryService/MetricReportDefinitions/CPUUtil/
/redfish/v1/TelemetryService/MetricReportDefinitions/MemoryBusUtil/
/redfish/v1/TelemetryService/MetricReportDefinitions/IOBusUtil/
/redfish/v1/TelemetryService/MetricReportDefinitions/CPUICUtil/
/redfish/v1/TelemetryService/MetricReportDefinitions/JitterCount/
/redfish/v1/TelemetryService/MetricReportDefinitions/PowerMetrics/
/redfish/v1/TelemetryService/MetricReportDefinitions/AvgCPU0Freq/
/redfish/v1/TelemetryService/MetricReportDefinitions/CPU0Power/
/redfish/v1/TelemetryService/MetricReportDefinitions/AvgCPU1Freq/
/redfish/v1/TelemetryService/MetricReportDefinitions/CPU1Power/
/redfish/v1/TelemetryService/MetricReportDefinitions/AvgCPU2Freq/
/redfish/v1/TelemetryService/MetricReportDefinitions/CPU2Power/
/redfish/v1/TelemetryService/MetricReportDefinitions/AvgCPU3Freq/
/redfish/v1/TelemetryService/MetricReportDefinitions/CPU3Power/
```

The eligibility rule is mechanical — enumerate the collection, drop `Custom`
URIs — so the list above is a snapshot, not a contract: newer firmware may add
members, and the filter, not the list, is what tooling should implement.

## Subscribing: iLO 5 and iLO 6 use different payload forms

Both generations POST an EventDestination to
`/redfish/v1/EventService/Subscriptions` naming the wanted report definitions
in `MetricReportDefinitions`, but they request metric-report delivery
differently — the divergence tooling must version on. The payloads below are
reproduced verbatim from the HPE portal, including its `Telemetryservice`
casing inside the example bodies (the canonical URI casing is
`TelemetryService`; iLO resolves both):

**iLO 5** — metric reports ride the `EventTypes` list:

```json
{
    "Destination": "https://myeventreciever/eventreceiver",
    "EventTypes": [
        "ResourceAdded", "ResourceRemoved", "ResourceUpdated",
        "StatusChange", "MetricReport", "Alert"
    ],
    "MetricReportDefinitions": [
        "/redfish/v1/Telemetryservice/MetricReportDefinitions/CPUUtil",
        "/redfish/v1/Telemetryservice/MetricReportDefinitions/CPU0Power",
        "/redfish/v1/Telemetryservice/MetricReportDefinitions/CPU1Power"
    ],
    "HttpHeaders": {"Header1": "HeaderValue1"},
    "Context": "context string",
    "Oem": {
        "Hpe": {
            "DeliveryRetryIntervalInSeconds": 30,
            "RequestedMaxEventsToQueue": 20,
            "DeliveryRetryAttempts": 5,
            "RetireOldEventInMinutes": 10
        }
    }
}
```

**iLO 6** — the modern DMTF form, `EventFormatType`:

```json
{
    "Destination": "https://myeventreciever/eventreceiver",
    "EventFormatType": "MetricReport",
    "MetricReportDefinitions": [
        "/redfish/v1/Telemetryservice/MetricReportDefinitions/CPUUtil",
        "/redfish/v1/Telemetryservice/MetricReportDefinitions/CPU0Power",
        "/redfish/v1/Telemetryservice/MetricReportDefinitions/CPU1Power"
    ],
    "HttpHeaders": {"Header": "HeaderValue"},
    "Context": "context string",
    "Oem": {
        "Hpe": {
            "DeliveryRetryIntervalInSeconds": 30,
            "RequestedMaxEventsToQueue": 20,
            "DeliveryRetryAttempts": 5,
            "RetireOldEventInMinutes": 10
        }
    }
}
```

The `Oem.Hpe` block tunes delivery on both generations: retry attempts and
interval, the receiver-side queue depth, and how long an undelivered event is
retained before retirement.

A created subscription reads back as an `EventDestination` resource (observed
`#EventDestination.v1_13_0` on iLO) with `MetricReportDefinitions` expanded to
`@odata.id` references, `Protocol: "Redfish"`, `SubscriptionType:
"RedfishEvent"`, and the `Oem.Hpe` delivery policy echoed
(`#HpeEventDestination.v2_1_0`).

## Scheduling a periodic report

A report definition is switched to periodic generation by PATCHing the
definition itself:

```text
PATCH /redfish/v1/TelemetryService/MetricReportDefinitions/CPUUtil/
```

```json
{
    "MetricReportDefinitionType": "Periodic",
    "Schedule": {
        "RecurrenceInterval": "P1DT",
        "InitialStartTime": "2023-06-01T01:00:00Z"
    }
}
```

`RecurrenceInterval` is an ISO-8601 duration; `InitialStartTime` anchors the
first report.

## How this anchors in `redfish_ctl`

Vendor telemetry behavior is a facet of the connection's vendor profile — the
same structure that owns status decode and task parsing — never a separate
vendor mechanism. For HPE that facet carries: the non-`Custom` eligibility
filter, the iLO 5 vs iLO 6 subscription form (selected the same way other
versioned vendor forms are selected), and the `Oem.Hpe` delivery-tuning block.
Subscription create/delete is a reversible configuration operation and follows
the standard capture, apply, verify, restore discipline.
