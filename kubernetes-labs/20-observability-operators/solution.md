# Lab 20 - Observability, SLOs, failure patterns & extending K8s (capstone) · **Solution**
**Patterns: Monitoring + Failure Patterns + Controller/Operator Source: DDS ch14/16; KIA 18; KP "Controller"/"Operator" Est:** 90 min

> The worked lab, with every command. Try the [exercise](lab.md) first; the checkpoint answers and the
> *why* are in [`lecture.md`](lecture.md).

## Objective
**See the system (metrics now; logs/traces conceptually), define reliability targets (an SLO and its error budget), handle failure correctly (retries with exponential backoff + jitter, and a circuit breaker - not a naive retry storm), and extend Kubernetes itself (a `Website` CRD plus a controller that materializes a Deployment+Service per CR - the Operator pattern). You will watch a symptom-based SLO burn-rate alert** fire under induced errors, then build the same reconcile loop that runs the whole control plane.

## Concepts exercised
- Prometheus **pull** scrape via a `ServiceMonitor` (the Operator turns a CRD into scrape config)
- The four pillars: **logs / metrics / traces / alerts**
- **RED (Rate / Errors / Duration) for request-driven services; USE** (Utilization / Saturation / Errors) for resources
- **SLI** (a measured good/total ratio), **SLO** (the target), **error budget** (1 − SLO)
- Multi-window, multi-burn-rate alerting via a `PrometheusRule` (fast burn = page, slow burn = ticket)
- Alerting on a **symptom** (user-facing SLO) vs a **cause** (CPU)
- OpenTelemetry **traces** show causality across services (concept + a simple chain)
- Failure patterns: retries + exponential backoff + jitter, **circuit breaker**, **thundering herd**, "absence of errors is an error" (alert on no-data), processing obsolete work
- **CRD + controller = Operator**: the reconcile loop (observe -> diff -> act)

## Prerequisites
- Labs **02** (Deployments), **03** (probes/lifecycle), **06** (Services & DNS), **13 (the adapter that standardizes `/metrics`). Labs 11** (singleton/leader), **18** (autoscaling), **19** (RBAC) are referenced in the lecture.
- A reachable cluster (`kubectl get nodes` -> 2-3 `Ready`). See `../00-cluster-setup/`.
- **kube-prometheus-stack** installed (Prometheus Operator -> the `ServiceMonitor`/`PrometheusRule` CRDs, Grafana, Alertmanager). See `../00-cluster-setup/eks.md §2.8` / `ovh.md §2.7`. If it is absent, the metrics/alert steps degrade gracefully to manual `curl` and you can still do the whole CRD/Operator section.

## Setup
```bash
kubectl create namespace lab-20-observability-operators
kubens lab-20-observability-operators      # or add -n lab-20-observability-operators to every command

# The system under observation + steady load:
kubectl apply -f manifests/podinfo.yaml
kubectl apply -f manifests/fortio-load.yaml
kubectl wait --for=condition=Available deploy/podinfo --timeout=90s

# Tell Prometheus to scrape podinfo (no-op if the Operator CRDs are absent):
kubectl apply -f manifests/servicemonitor.yaml || echo "no ServiceMonitor CRD -> skipping; use curl in step 1"
```
**Predict (0):** podinfo exposes `/metrics` itself (it is already instrumented; lab 13 showed you the *adapter* trick for apps that are not). Before you apply anything to Prometheus - does applying a `ServiceMonitor` change *podinfo*, or does it change *Prometheus*? Which component actually reloads?

---

## Steps

### 1. See the metrics (the raw pull endpoint)
Prometheus does not receive pushes; it **pulls** `/metrics` on an interval. Look at what it pulls:
```bash
kubectl exec deploy/fortio-load -- fortio curl -quiet http://podinfo:9898/metrics 2>/dev/null \
  | grep -E '^# (HELP|TYPE) http_requests_total|^http_requests_total' | head -8
```
**Observe (1): You see `http_requests_total{...status="200"...}` climbing (that is your Rate** and **Errors by status) and `http_request_duration_seconds` histogram buckets (your Duration**). That is the whole of **RED** in one endpoint.

### 2. Confirm Prometheus is scraping podinfo (target UP)
Only if kube-prometheus-stack is installed. Port-forward Prometheus:
```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090 &
# Browse http://localhost:9090/targets  -> find serviceMonitor/.../podinfo -> State: UP
# Or query the API:
curl -s 'http://localhost:9090/api/v1/targets' | grep -o '"job":"[^"]*podinfo[^"]*"' | head
```
**Observe (2):** The podinfo target shows **UP**. You wrote no `prometheus.yml` - the Operator reconciled your `ServiceMonitor` CRD into scrape config and hot-reloaded Prometheus.

### 3. Query the RED signals (the dashboard, as PromQL)
In the Prometheus UI (`http://localhost:9090/graph`) run:
```promql
# Rate (requests/sec):
sum(rate(http_requests_total{namespace="lab-20-observability-operators"}[1m]))
# Errors (5xx ratio = the SLI):
sum(rate(http_requests_total{namespace="lab-20-observability-operators",status=~"5.."}[1m]))
  / sum(rate(http_requests_total{namespace="lab-20-observability-operators"}[1m]))
# Duration (p99 latency from the histogram):
histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{namespace="lab-20-observability-operators"}[5m])) by (le))
```
**Observe (3):** Under steady load: Rate is steady, the error ratio is ~`0`, p99 is low. Grafana ships a generic dashboard that is exactly these three lines. (Grafana: `kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80`; default creds in the chart's docs.)

### 4. Define the SLO + the burn-rate alert
Open `manifests/prometheusrule.yaml` and read it: the SLO is **99% of requests succeed**, so the **error budget is 1%**. The alert does **not trigger on a static threshold - it triggers on how fast the budget is burning** (fast burn -> page, slow burn -> ticket), with a short+long window pairing to stop flapping.
```bash
kubectl apply -f manifests/prometheusrule.yaml || echo "no PrometheusRule CRD -> skipping alert steps"
# It loads into Prometheus within ~30s:
curl -s 'http://localhost:9090/api/v1/rules' | grep -o 'PodinfoErrorBudgetBurn[A-Za-z]*' | sort -u
```
**Predict (4):** Right now (no errors), are `PodinfoErrorBudgetBurnFast` and `PodinfoErrorBudgetBurnSlow` `inactive`, `pending`, or `firing`? Check:
```bash
kubectl get prometheusrule podinfo-slo -o name
curl -s 'http://localhost:9090/api/v1/alerts' | grep -o '"alertname":"Podinfo[^"]*","[^}]*"state":"[^"]*"'
```

### 5. Drive errors -> burn the budget -> the alert FIRES
This is the symptom the SLO alert exists to catch: real user-visible 5xx.
```bash
kubectl apply -f manifests/fortio-errors.yaml      # hammers podinfo /status/500
# Watch the SLI climb past the 1% budget:
#   (Prometheus UI) sum(rate(http_requests_total{status=~"5.."}[1m])) / sum(rate(http_requests_total[1m]))
# Within ~2-3 min the FAST burn alert moves inactive -> pending -> firing:
watch -n 5 "curl -s 'http://localhost:9090/api/v1/alerts' | grep -o '\"alertname\":\"PodinfoErrorBudgetBurnFast\",[^}]*\"state\":\"[^\"]*\"'"
```
**Predict (5):** Which fires first - `...BurnFast` (page) or `...BurnSlow` (ticket)? Why does the fast one win when errors arrive in a sudden flood?

**Prove it (5):** In Alertmanager (`kubectl -n monitoring port-forward svc/kube-prometheus-stack-alertmanager 9093:9093`, browse `http://localhost:9093`) the `PodinfoErrorBudgetBurnFast` alert appears with `severity=page`. Stop the errors and watch it resolve:
```bash
kubectl delete -f manifests/fortio-errors.yaml
# error ratio falls back to ~0; the alert returns to inactive within a few minutes.
```

### 6. The right way to retry - backoff + jitter + circuit breaker (concept made observable)
A failing dependency tempts every client to retry. Done naively, retries *amplify* the outage. See it, then fix it. (Covered in depth in **Break it** below; the manifests are `herd-naive.yaml` and `herd-backoff.yaml`.) Production client libraries (gRPC, the AWS SDK, resilience4j, Polly) and proxies (Envoy/ambassador from lab 13) implement backoff+jitter and a circuit breaker for you.

### 7. Traces (concept + a one-command chain)
Metrics tell you *that* p99 is bad; a **trace** tells you *which hop* caused it. podinfo can call a downstream and propagate trace context. Generate a two-hop request:
```bash
# podinfo's /delay/{sec} simulates a slow downstream hop; chain two calls:
kubectl exec deploy/fortio-load -- fortio curl -quiet http://podinfo:9898/delay/1 2>/dev/null | head -3
```
**Observe (7): The response took ~1s. With OpenTelemetry instrumentation (podinfo emits spans when `--otel` is configured), this single request becomes a trace: a tree of spans showing the 1s was spent in the downstream hop, not the edge. Metrics aggregate; traces preserve causality** for one request. (Full tracing needs a collector + Tempo/Jaeger - out of scope to install; the concept is the deliverable.)

### 8. Extend Kubernetes - the CRD (a new kind, but inert)
```bash
kubectl apply -f manifests/website-crd.yaml
kubectl get crd websites.example.com
kubectl explain website.spec               # the API server now documents YOUR kind
kubectl get websites                       # works, but: "No resources found" -- nothing yet
```
**Predict (8): You just taught the API server a `Website` kind. If you create a `Website` CR right now** (before any controller exists), will any pods appear?

### 9. The controller - your reconcile loop
```bash
kubectl apply -f manifests/controller-rbac.yaml    # SA + Role + RoleBinding (least privilege)
kubectl apply -f manifests/controller.yaml         # bitnami/kubectl pod running reconcile.sh
kubectl wait --for=condition=Available deploy/website-controller --timeout=90s
kubectl logs deploy/website-controller --tail=5    # "starting reconcile loop in namespace=..."
```
Read `manifests/controller.yaml` - the loop is ~60 lines of bash: list Websites (desired) -> for each, apply a Deployment+Service (act) -> delete children whose Website is gone (garbage collect) -> sleep -> repeat.
**Observe (9):** With no Website CRs yet, the controller idles (the desired set is empty). It is *watching*, not acting.

### 10. Create a Website CR -> the controller materializes a Deployment+Service
```bash
kubectl apply -f manifests/website-cr.yaml
sleep 8
kubectl get website hello-site                                   # Phase: Ready
kubectl get deploy,svc -l created-by=website-controller          # hello-site Deployment + Service APPEARED
kubectl logs deploy/website-controller --tail=3                  # "reconciled website/hello-site -> deploy+svc"
```
**Prove it (10):** You created **one** `Website` object and a Deployment + Service you never wrote now exist, owned and labeled by the controller. That is the Operator pattern: a domain CRD + a reconcile loop encoding "how to run a website."

### 11. Edit the CR -> the loop converges; the controller self-heals
```bash
kubectl patch website hello-site --type=merge -p '{"spec":{"replicas":3}}'
sleep 8
kubectl get deploy hello-site -o jsonpath='{.spec.replicas}'; echo     # -> 3
# Now delete a child by hand and watch the loop put it back:
kubectl delete svc hello-site
sleep 8
kubectl get svc hello-site                                              # recreated by the controller
```
**Prove it (11):** Editing desired state (replicas 2->3) reconciled the child Deployment; deleting a child out-of-band was *undone* on the next pass. Reconciliation is continuous, not one-shot - exactly why "I deleted it but it came back" happens (lab 01's lesson, now from the controller side).

---

## Verify
```bash
# Prometheus scrapes podinfo (only if the stack is installed):
curl -s 'http://localhost:9090/api/v1/targets' | grep -o '"health":"up"' | head -1     # -> "health":"up"
# The burn-rate alert exists (and fired during step 5):
kubectl get prometheusrule podinfo-slo
# The Operator works -- a CR materialized real children:
kubectl get website hello-site
kubectl get deploy,svc -l created-by=website-controller                                 # hello-site deploy + svc
# Deleting the CR garbage-collects them:
kubectl delete -f manifests/website-cr.yaml
sleep 8
kubectl get deploy,svc -l created-by=website-controller                                 # -> No resources found
```
yes Success = podinfo target UP in Prometheus; the SLO burn-rate alert transitioned to FIRING under induced 5xx and resolved when they stopped; creating a `Website` CR auto-created its Deployment+Service and deleting it removed them.

---

## Break it - thundering herd, then the fix; and cause-vs-symptom alerting

### B1 - Naive retries amplify an outage (thundering herd)
A dependency is failing. Twelve clients retry in **lockstep** with a **fixed** gap and **no jitter**:
```bash
kubectl apply -f manifests/herd-naive.yaml
# Watch the synchronized retry waves in Prometheus (rate against the failing path):
#   sum(rate(http_requests_total{namespace="lab-20-observability-operators",status=~"5.."}[30s]))
kubectl get pods -l app.kubernetes.io/name=herd-naive
```
**Predict (B1):** All 12 clients fail at the same instant and wait the *same* 1s. Will their retries spread out over time, or pulse together? What does that do to a service that is *trying to recover*?

**Observe (B1): The error-request rate stays high and spiky** - it does not decay. Synchronized retries pin load at full amplitude; each time the backend tries to recover, the next wave knocks it back. The retries *are* the outage now (retry amplification). This is the **thundering herd**.

### B2 - The fix: exponential backoff + jitter + circuit breaker
```bash
kubectl delete -f manifests/herd-naive.yaml
kubectl apply -f manifests/herd-backoff.yaml
kubectl logs -l app.kubernetes.io/name=herd-backoff --tail=20 --prefix | head -30
```
**Observe (B2):** Each client's `backoff` grows (1->2->4->8->16s) and the `jittered` sleep is a *random* fraction of it, so the 12 clients **desynchronize**. The aggregate retry rate **decays instead of pulsing, and after 5 straight failures a client's breaker OPENs** (it stops calling for a cooldown, then sends one trial). Backoff caps amplitude; jitter spreads it in time; the breaker stops hammering a service that is clearly down. Compare the request-rate shape against B1.

**Prove it (B2):** Backoff + jitter + breaker turns a self-reinforcing herd into a gentle, decaying probe. This is what every production retry library does - never hand-roll a bare `while; retry; sleep(const)`.

### B3 - Alert on the SYMPTOM, not the CAUSE
`prometheusrule.yaml` also ships `PodinfoHighCPU`, a deliberately **inferior** cause-based alert.
```bash
# 1) A CAUSE alert false-fires when users are FINE: drive heavy-but-successful load.
kubectl exec deploy/fortio-load -- fortio load -qps 0 -t 20s -c 16 http://podinfo:9898/delay/0 >/dev/null 2>&1 &
#    CPU climbs -> PodinfoHighCPU may fire, but the SLO burn alert stays quiet (0 errors). A NEEDLESS page.
# 2) A SYMPTOM that uses NO CPU: a 100%-error outage that is cheap to serve.
kubectl apply -f manifests/fortio-errors.yaml
#    SLO burn-rate FIRES (users see 5xx); PodinfoHighCPU does NOT (errors are cheap). The cause alert is BLIND to the real outage.
kubectl delete -f manifests/fortio-errors.yaml
```
**Predict (B3):** Which alert do you want waking you at 3am - the one tied to CPU, or the one tied to "users are getting errors"? Which one both *false-alarms* on healthy load **and** *stays silent* during a real outage?

**Observe (B3): The cause-based alert does both failures: it pages on healthy-but-busy load and misses a cheap-to-serve outage. The symptom-based SLO alert tracks exactly what the user feels. Alert on symptoms (user-facing SLOs); use causes for dashboards and diagnosis, not paging.**

---

## Cleanup
```bash
# Namespaced objects (podinfo, fortio, controller, RBAC, ServiceMonitor, PrometheusRule, Website CRs):
kubectl delete namespace lab-20-observability-operators

# CLUSTER-SCOPED leftovers -- the namespace delete does NOT remove these:
kubectl delete crd websites.example.com            # the CRD is cluster-scoped
# If you switched the controller RBAC to a ClusterRole/ClusterRoleBinding, delete them too:
# kubectl delete clusterrole website-controller clusterrolebinding website-controller 2>/dev/null || true
```
> The controller's `Role`/`RoleBinding`/`ServiceAccount` are namespaced and go with the namespace. The **CRD is cluster-scoped - always delete it explicitly or `kubectl get websites` keeps working (and a future `apply` may conflict). kube-prometheus-stack is shared infrastructure from cluster-setup; leave it installed** for other labs. No cloud LoadBalancer or volume was created in this lab.

---
*Now read [`lecture.md`](lecture.md) and grade your Predict predictions.*
