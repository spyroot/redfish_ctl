{{- define "redfish-controller.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "redfish-controller.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := include "redfish-controller.name" . -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "redfish-controller.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- include "redfish-controller.fullname" . -}}
{{- else -}}
{{- required "serviceAccount.name is required when serviceAccount.create=false" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "redfish-controller.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "redfish-controller.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "redfish-controller.selectorLabels" -}}
app.kubernetes.io/name: {{ include "redfish-controller.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "redfish-controller.image" -}}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag -}}
{{- end -}}

{{- define "redfish-controller.mockBmcName" -}}
{{- printf "%s-mock-bmc" (include "redfish-controller.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "redfish-controller.mockBmcImage" -}}
{{- printf "%s:%s" .Values.mockBmc.image.repository .Values.mockBmc.image.tag -}}
{{- end -}}
