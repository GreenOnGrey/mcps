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

{{- /* mcp.atlassian renders Jira or Confluence from one image (FTR.INFRA.MCPS-0001 R3, R7).
       Takes a dict: name ("jira"|"confluence"), cfg (the values block), root (the chart root). */ -}}
{{- define "mcp.atlassian" -}}
{{- $name := .name -}}
{{- $cfg := .cfg -}}
{{- $root := .root -}}
{{- $env := upper $name -}}
{{- $host := "" -}}
{{- with $cfg.url }}{{ $host = (urlParse .).host }}{{ end -}}
# Read-only {{ $name }} (FTR.INFRA.MCPS-0001): mcp-atlassian from our image. The token of the user arrives
# in the header of every request from the proxy of Nabu: an OAuth access token of Atlassian Cloud or a
# personal access token of Data Center. The deployment enables one service of the two.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ $name }}
  labels: {{- include "mcp.labels" $root | nindent 4 }}
    app.kubernetes.io/name: {{ $name }}
spec:
  replicas: 1
  selector: { matchLabels: { app.kubernetes.io/name: {{ $name }} } }
  template:
    metadata:
      labels: {{- include "mcp.labels" $root | nindent 8 }}
        app.kubernetes.io/name: {{ $name }}
    spec:
      {{- include "mcp.podSecurity" $root | nindent 6 }}
      {{- with $root.Values.imagePullSecrets }}
      imagePullSecrets: {{- toYaml . | nindent 8 }}
      {{- end }}
      containers:
        - name: {{ $name }}
          image: {{ $cfg.image }}
          ports: [{ name: http, containerPort: 8000 }]
          env:
            - { name: {{ $env }}_URL, value: {{ $cfg.url | quote }} }
            - { name: {{ $env }}_SSL_VERIFY, value: "true" }
            # write tools are hidden at tools/list and refused at tools/call (R3)
            - { name: READ_ONLY_MODE, value: "true" }
            # the address of the system is in a private network, which the upstream refuses by default
            - { name: MCP_ALLOWED_URL_DOMAINS, value: {{ $host | quote }} }
            # truststore replaces the SSL context of the process and breaks a bundle of a private CA
            - { name: MCP_ATLASSIAN_USE_SYSTEM_TRUSTSTORE, value: "false" }
            {{- if eq $cfg.authMode "cloud" }}
            - { name: ATLASSIAN_OAUTH_ENABLE, value: "true" }
            - { name: ATLASSIAN_OAUTH_CLOUD_ID, value: {{ $cfg.cloudId | quote }} }
            {{- else }}
            # the upstream treats the service as configured only with a non-empty token; the real one comes per request
            - { name: {{ $env }}_PERSONAL_TOKEN, value: "per-request" }
            {{- end }}
            {{- with $cfg.caSecret }}
            - { name: REQUESTS_CA_BUNDLE, value: /etc/mcp-ca/ca.crt }
            - { name: SSL_CERT_FILE, value: /etc/mcp-ca/ca.crt }
            {{- end }}
          # /healthz of the upstream answers slowly under load: a restart of a healthy pod is worse than a late answer
          startupProbe: { httpGet: { path: /healthz, port: http }, periodSeconds: 5, failureThreshold: 60 }
          readinessProbe: { httpGet: { path: /healthz, port: http }, periodSeconds: 15, timeoutSeconds: 10, failureThreshold: 5 }
          livenessProbe: { httpGet: { path: /healthz, port: http }, periodSeconds: 30, timeoutSeconds: 10, failureThreshold: 5 }
          resources: {{- toYaml $cfg.resources | nindent 12 }}
          {{- include "mcp.containerSecurity" $root | nindent 10 }}
          volumeMounts:
            - { name: tmp, mountPath: /tmp }
            {{- with $cfg.caSecret }}
            - { name: ca, mountPath: /etc/mcp-ca, readOnly: true }
            {{- end }}
      volumes:
        - { name: tmp, emptyDir: {} }
        {{- with $cfg.caSecret }}
        - name: ca
          secret: { secretName: {{ . }}, defaultMode: 0444 }
        {{- end }}
---
apiVersion: v1
kind: Service
metadata:
  name: {{ $name }}
  labels: {{- include "mcp.labels" $root | nindent 4 }}
spec:
  selector: { app.kubernetes.io/name: {{ $name }} }
  ports: [{ name: http, port: 8000, targetPort: http }]
{{- end }}
