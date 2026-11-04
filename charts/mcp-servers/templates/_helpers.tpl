{{- define "mcp.labels" -}}
app.kubernetes.io/part-of: mcp-servers
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end }}

{{- define "mcp.podSecurity" -}}
automountServiceAccountToken: false
securityContext:
  runAsNonRoot: true
  seccompProfile: { type: RuntimeDefault }
{{- end }}

{{- define "mcp.containerSecurity" -}}
securityContext:
  runAsNonRoot: true
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities: { drop: [ALL] }
{{- end }}
