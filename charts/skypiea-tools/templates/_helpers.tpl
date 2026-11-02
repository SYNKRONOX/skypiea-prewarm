{{- define "skypiea.k8sName" -}}{{ . | replace "_" "-" }}{{- end -}}

{{- define "skypiea.labels" -}}
app.kubernetes.io/part-of: skypiea
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
helm.sh/chart: {{ .root.Chart.Name }}-{{ .root.Chart.Version }}
skypiea.io/tool: {{ .name }}
{{- end -}}

{{- define "skypiea.image" -}}{{ .root.Values.registry }}skypiea/{{ .name }}:{{ .root.Values.imageTag }}{{- end -}}
