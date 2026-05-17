# Lab 20 - Observability, SLOs, failure patterns & extending K8s (capstone) · **Exercise**
**Patterns: Monitoring + Failure Patterns + Controller/Operator Source: DDS ch14/16; KIA 18; KP "Controller"/"Operator" Est:** 90 min

> **This is the exercise - the commands are deliberately *not* given.** Your job is to work out the
> `kubectl`, PromQL, and `curl` invocations yourself; that *is* the skill. Attempt every task and write
> down your answer to every **Predict / Observe / Prove it / Break it** before peeking. When you're stuck or done,
> [`solution.md`](solution.md) has the exact commands + the output you should have seen + every checkpoint
> answer. Then read [`lecture.md`](lecture.md) for the course. This is the finale: it ties together probes
> (lab 03), PDB/singletons (lab 11), the adapter `/metrics` (lab 13), autoscaling latency (lab 18), and
> RBAC (lab 19).

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
- A reachable cluster (2-3 `Ready` nodes). See `../00-cluster-setup/`.
- **kube-prometheus-stack** installed (Prometheus Operator -> the `ServiceMonitor`/`PrometheusRule` CRDs, Grafana, Alertmanager). See `../00-cluster-setup/eks.md §2.8` / `ovh.md §2.7`. If it is absent, the metrics/alert steps degrade gracefully to manual `curl` and you can still do the whole CRD/Operator section.

## Setup
Create a namespace **`lab-20-observability-operators`** and make it the default for the rest of this lab. Deploy the system under observation by applying `manifests/podinfo.yaml` and the steady-load generator `manifests/fortio-load.yaml`, and wait until the podinfo Deployment is `Available`. Then register podinfo as a Prometheus scrape target by applying `manifests/servicemonitor.yaml` (this is a no-op if the Operator CRDs are absent - fall back to `curl` in task 1).

**Predict (0):** podinfo exposes `/metrics` itself (it is already instrumented; lab 13 showed you the *adapter* trick for apps that are not). Before you apply anything to Prometheus - does applying a `ServiceMonitor` change *podinfo*, or does it change *Prometheus*? Which component actually reloads?

---

## Tasks

### 1. See the metrics (the raw pull endpoint)
Prometheus does not receive pushes; it **pulls** `/metrics` on an interval. From inside the fortio-load pod, fetch podinfo's `/metrics` endpoint (port 9898) and isolate the `http_requests_total` counter and its HELP/TYPE lines so you can see what Prometheus will scrape.

**Observe (1): Find `http_requests_total{...status="200"...}` climbing (that is your Rate** and **Errors by status) and the `http_request_duration_seconds` histogram buckets (your Duration). Convince yourself that the whole of RED** lives in this one endpoint.

### 2. Confirm Prometheus is scraping podinfo (target UP)
Only if kube-prometheus-stack is installed. Reach the Prometheus server (port 9090 in the `monitoring` namespace) and find the podinfo target - either in the Targets UI or via the targets API.

**Observe (2):** The podinfo target reads **UP**. You wrote no `prometheus.yml` by hand - explain what turned your `ServiceMonitor` CRD into live scrape config and what triggered the reload.

### 3. Query the RED signals (the dashboard, as PromQL)
In the Prometheus UI, write three PromQL queries scoped to namespace `lab-20-observability-operators`: the request **Rate** (per second), the **Errors SLI (the ratio of 5xx responses to all responses), and the Duration** as p99 latency derived from the `http_request_duration_seconds` histogram buckets.

**Observe (3):** Under steady load, predict and then confirm the shape of each signal (Rate, error ratio, p99). Note that Grafana's generic dashboard is exactly these three lines.

### 4. Define the SLO + the burn-rate alert
Open `manifests/prometheusrule.yaml` and read it: the SLO is **99% of requests succeed**, so the **error budget is 1%**. The alert does **not trigger on a static threshold - it triggers on how fast the budget is burning** (fast burn -> page, slow burn -> ticket), with a short+long window pairing to stop flapping. Apply the `PrometheusRule` and confirm both burn-rate rules (`PodinfoErrorBudgetBurnFast` and `PodinfoErrorBudgetBurnSlow`) have loaded into Prometheus.

**Predict (4):** Right now (no errors), are `PodinfoErrorBudgetBurnFast` and `PodinfoErrorBudgetBurnSlow` `inactive`, `pending`, or `firing`? Confirm by querying the alerts API for their current state.

### 5. Drive errors -> burn the budget -> the alert FIRES
This is the symptom the SLO alert exists to catch: real user-visible 5xx. Apply `manifests/fortio-errors.yaml` (it hammers podinfo's `/status/500` path), then watch the error-ratio SLI climb past the 1% budget and the **fast** burn alert transition `inactive` -> `pending` -> `firing` over ~2-3 minutes.

**Predict (5):** Which fires first - `...BurnFast` (page) or `...BurnSlow` (ticket)? Why does the fast one win when errors arrive in a sudden flood?

**Prove it (5):** In Alertmanager, confirm the `PodinfoErrorBudgetBurnFast` alert appears with `severity=page`. Then delete the error generator and watch the error ratio fall back toward 0 and the alert return to inactive within a few minutes.

### 6. The right way to retry - backoff + jitter + circuit breaker (concept made observable)
A failing dependency tempts every client to retry. Done naively, retries *amplify* the outage. This is explored hands-on in **Break it** below (manifests `herd-naive.yaml` and `herd-backoff.yaml`). Note that production client libraries (gRPC, the AWS SDK, resilience4j, Polly) and proxies (Envoy/ambassador from lab 13) implement backoff+jitter and a circuit breaker for you.

### 7. Traces (concept + a one-command chain)
Metrics tell you *that* p99 is bad; a **trace** tells you *which hop* caused it. podinfo's `/delay/{sec}` path simulates a slow downstream hop. From the fortio-load pod, issue a request to podinfo's `/delay/1` and observe the latency.

**Observe (7): Confirm the response took ~1s. Explain how, with OpenTelemetry instrumentation, this single request would become a trace - a tree of spans attributing the 1s to the downstream hop rather than the edge - and why metrics aggregate while traces preserve causality** for one request. (Full tracing needs a collector + Tempo/Jaeger - out of scope to install; the concept is the deliverable.)

### 8. Extend Kubernetes - the CRD (a new kind, but inert)
Apply `manifests/website-crd.yaml` to register the `websites.example.com` CRD. Confirm the CRD exists, that the API server now documents your `Website` kind (via `kubectl explain website.spec`), and that listing `websites` works but returns nothing yet.

**Predict (8): You just taught the API server a `Website` kind. If you create a `Website` CR right now** (before any controller exists), will any pods appear?

### 9. The controller - your reconcile loop
Apply `manifests/controller-rbac.yaml` (a ServiceAccount + Role + RoleBinding, least privilege) and `manifests/controller.yaml` (a `bitnami/kubectl` pod running `reconcile.sh`), wait until the `website-controller` Deployment is `Available`, and read its startup logs. Read `manifests/controller.yaml`: the loop is ~60 lines of bash - list Websites (desired) -> for each, apply a Deployment+Service (act) -> delete children whose Website is gone (garbage collect) -> sleep -> repeat.

**Observe (9):** With no Website CRs yet, the controller idles (the desired set is empty). Confirm it is *watching*, not acting.

### 10. Create a Website CR -> the controller materializes a Deployment+Service
Apply `manifests/website-cr.yaml`, give the loop a few seconds, then confirm three things: the `hello-site` Website reports `Phase: Ready`; a Deployment and Service labeled `created-by=website-controller` have appeared; and the controller logs record that it reconciled `website/hello-site`.

**Prove it (10):** You created **one** `Website` object and a Deployment + Service you never wrote now exist, owned and labeled by the controller. That is the Operator pattern: a domain CRD + a reconcile loop encoding "how to run a website."

### 11. Edit the CR -> the loop converges; the controller self-heals
Patch the `hello-site` Website to set `spec.replicas` to 3 and confirm the child Deployment's replica count follows. Then delete the child Service by hand, wait a few seconds, and confirm the controller recreated it on its next pass.

**Prove it (11):** Editing desired state (replicas 2->3) reconciled the child Deployment; deleting a child out-of-band was *undone* on the next pass. Reconciliation is continuous, not one-shot - exactly why "I deleted it but it came back" happens (lab 01's lesson, now from the controller side).

---

## Verify
Demonstrate success with observable signals: the podinfo target reads UP/healthy in Prometheus (if the stack is installed); the `podinfo-slo` `PrometheusRule` exists; the `hello-site` Website materialized a Deployment + Service labeled `created-by=website-controller`; and deleting the Website CR garbage-collects those children (after a short pause, nothing labeled `created-by=website-controller` remains).

yes Success = podinfo target UP in Prometheus; the SLO burn-rate alert transitioned to FIRING under induced 5xx and resolved when they stopped; creating a `Website` CR auto-created its Deployment+Service and deleting it removed them.

---

## Break it - thundering herd, then the fix; and cause-vs-symptom alerting

### B1 - Naive retries amplify an outage (thundering herd)
A dependency is failing. Apply `manifests/herd-naive.yaml`: twelve clients retry in **lockstep** with a **fixed** gap and **no jitter**. Watch the 5xx request rate against the failing path in Prometheus over a short window, and list the herd pods.

**Predict (B1):** All 12 clients fail at the same instant and wait the *same* 1s. Will their retries spread out over time, or pulse together? What does that do to a service that is *trying to recover*?

**Observe (B1): Confirm the error-request rate stays high and spiky** - it does not decay. Synchronized retries pin load at full amplitude; each time the backend tries to recover, the next wave knocks it back. The retries *are* the outage now (retry amplification). This is the **thundering herd**.

### B2 - The fix: exponential backoff + jitter + circuit breaker
Delete the naive herd and apply `manifests/herd-backoff.yaml`. Read the backoff clients' logs across all pods.

**Observe (B2):** Confirm each client's `backoff` grows (1->2->4->8->16s) and the `jittered` sleep is a *random* fraction of it, so the 12 clients **desynchronize**. The aggregate retry rate **decays instead of pulsing, and after 5 straight failures a client's breaker OPENs** (it stops calling for a cooldown, then sends one trial). Backoff caps amplitude; jitter spreads it in time; the breaker stops hammering a service that is clearly down. Compare the request-rate shape against B1.

**Prove it (B2):** Backoff + jitter + breaker turns a self-reinforcing herd into a gentle, decaying probe. This is what every production retry library does - never hand-roll a bare retry-then-sleep-constant loop.

### B3 - Alert on the SYMPTOM, not the CAUSE
`prometheusrule.yaml` also ships `PodinfoHighCPU`, a deliberately **inferior cause-based alert. Run two contrasting experiments: first drive heavy-but-successful load (e.g. concurrent requests against podinfo's `/delay/0`) so CPU climbs while errors stay at zero; then induce a cheap 100%-error** outage by applying `manifests/fortio-errors.yaml` (then remove it). Observe which alert fires in each case.

**Predict (B3):** Which alert do you want waking you at 3am - the one tied to CPU, or the one tied to "users are getting errors"? Which one both *false-alarms* on healthy load **and** *stays silent* during a real outage?

**Observe (B3): Confirm the cause-based alert does both failures: it pages on healthy-but-busy load and misses a cheap-to-serve outage. The symptom-based SLO alert tracks exactly what the user feels. Alert on symptoms (user-facing SLOs); use causes for dashboards and diagnosis, not paging.**

---

## Cleanup
Delete the `lab-20-observability-operators` namespace - that removes all the namespaced objects (podinfo, fortio, the controller, its `Role`/`RoleBinding`/`ServiceAccount`, the `ServiceMonitor`, the `PrometheusRule`, and the Website CRs). Then clean up the **cluster-scoped leftovers that a namespace delete does not** touch: delete the `websites.example.com` CRD, and if you ever switched the controller's RBAC to a `ClusterRole`/`ClusterRoleBinding`, delete those too.

> The **CRD is cluster-scoped - always delete it explicitly or `kubectl get websites` keeps working (and a future apply may conflict). kube-prometheus-stack is shared infrastructure from cluster-setup; leave it installed** for other labs. No cloud LoadBalancer or volume was created in this lab.

---
*Stuck or finished? -> [`solution.md`](solution.md) for the worked commands & answers, then [`lecture.md`](lecture.md) for the course.*
