# Kubernetes + SRE Mastery - Curriculum Build Spec (Handoff)

> **Purpose of this file.** This is a complete, self-contained execution spec for building a 20-lab
> Kubernetes + SRE course. Any agent should be able to pick this up and produce the course **without
> re-reading the source books or re-asking the user**. Everything decided so far is recorded here:
> mission, constraints, source material, conventions, demo apps, cluster setup (EKS + OVH), the exact
> lab/lecture templates, and a full per-lab breakdown including the five lecture "lenses."
>
> Status: plan approved; no lab files generated yet. Build on request.

---

## 0. Mission & hard constraints

**Mission. Take the learner to ~80% practical mastery of Kubernetes + its design patterns + SRE
practice** through 20 hands-on labs on a managed cluster, each followed by a **lecture** that
builds on the lab and explains it from five senior lenses.

Constraints (locked by the user - do not relitigate):

1. Platform = EKS primary, OVHcloud Managed Kubernetes (MKS) secondary. Every lab's manifests
 target a generic conformant cluster; cloud-specific bits (LoadBalancer, CSI/StorageClass,
 autoscaler, identity) are given for **both** EKS and OVH in dedicated sub-sections. Never assume a
 cloud-only primitive without giving the OVH equivalent.
2. Labs are CPU-only. Kubernetes mastery is the objective, not GPU/ML. No lab requests
 `nvidia.com/gpu`, no GPU node pool is required to complete any lab. GPU/LLM/ML content lives
 only in the lecture "AI/ML notes" lens as conceptual mapping (how the pattern applies to vLLM /
 KServe / inference serving). This keeps cost ~zero and removes hardware gating.
3. **Progressive but not monolithic.** Labs are ordered so concepts compound (later labs assume earlier
 knowledge), but each lab is **self-contained and resettable** - it runs in its own namespace and
 `cleanup` removes everything. There is no single app threaded through all 20; instead a small set
 of reusable demo apps (see §4) is used where convenient.
4. **Deliverable cadence:** the user asked for "the plan .md first." When building, generate in reviewable
 batches (see §9), not one giant dump.

Definition of done for the whole course: every K8s object family, every SRE pillar, and every
pattern family from the three books is touched by ≥1 lab (verified by `coverage-matrix.md`, §8); every
`lab.md` runs clean on a fresh namespace on both EKS and OVH; every lecture cites its source chapter.

---

## 1. Source material (already on disk)

Three books were extracted to markdown under `/home/talium/LEARN/kubernetes_book/` in a
`Part NN / Chapter NN / NN - section.md` hierarchy. Use them as the authoritative reference when
writing lecture prose - cite chapter names.

| Code | Book | Folder | Role in course |
|------|------|--------|----------------|
| **KIA** | *Kubernetes in Action* - Luksa, Manning 2018 | `Kubernetes in Action/` | Core mechanics - the "how". 18 chapters. |
| **KP** | *Kubernetes Patterns* - Ibryam & Huß, O'Reilly 2nd ed 2023 | `Kubernetes Patterns/` | 30 reusable patterns in 6 parts - the "shape/why". |
| **DDS** | *Designing Distributed Systems* - Burns, O'Reilly 2nd ed 2025 | `Designing Distributed Systems/` | Distributed/serving/batch patterns + observability + failure patterns - the "system why". |

### 1.1 KIA chapter index (cite these)
1 Intro · 2 First steps (Docker/kubectl) · 3 Pods (labels, selectors, namespaces) · 4 Replication
(RC/RS, DaemonSet, Job, CronJob, liveness) · 5 Services (ClusterIP/NodePort/LB, Ingress, readiness,
headless) · 6 Volumes (emptyDir, hostPath, PV/PVC, StorageClass) · 7 ConfigMaps & Secrets · 8 Downward
API / talking to API server · 9 Deployments (rolling update, rollback) · 10 StatefulSets · 11 Internals
(etcd, API server, scheduler, controllers, kubelet, kube-proxy, HA) · 12 Securing API server
(authn, ServiceAccounts, RBAC) · 13 Securing nodes/network (securityContext, PSP, NetworkPolicy) ·
14 Resource management (requests/limits, QoS, LimitRange, ResourceQuota) · 15 Autoscaling (HPA, VPA,
Cluster Autoscaler) · 16 Advanced scheduling (taints/tolerations, affinity) · 17 Best practices
(lifecycle hooks, graceful shutdown, init containers) · 18 Extending K8s (CRD, controllers, operators).

### 1.2 KP pattern index (6 parts, 30 patterns - cite these)
- **Foundational:** Predictable Demands · Declarative Deployment · Health Probe · Managed Lifecycle · Automated Placement.
- **Behavioral:** Batch Job · Periodic Job · Daemon Service · Singleton Service · Stateless Service · Stateful Service · Service Discovery · Self Awareness.
- **Structural:** Init Container · Sidecar · Adapter · Ambassador.
- **Configuration:** EnvVar Config · Configuration Resource · Immutable Configuration · Configuration Template.
- **Security:** Process Containment · Network Segmentation · Secure Configuration · Access Control.
- **Advanced:** Controller · Operator · Elastic Scale · Image Builder.

### 1.3 DDS pattern index (cite these)
- **Foundational concepts:** APIs/RPC, latency & percentiles, reliability/error semantics, idempotency & delivery semantics, CAP/consistency, health checks.
- **Single-node:** Sidecar · Ambassador · Adapter.
- **Serving:** Replicated Load-Balanced Services · Sharded Services · Scatter/Gather · FaaS/Event-driven · Ownership Election (leader election / leases).
- **Batch:** Work Queue · Event-Driven Batch (copier/filter/splitter/sharder/merger) · Coordinated Batch (map/shuffle/reduce).
- **Universal:** Monitoring & Observability (logs/metrics/traces/alerts) · AI inference serving (brief) · Failure patterns (thundering herd, retries+jitter+circuit breaker, "absence of errors is an error", versioning, cascading deletes, processing obsolete work, second-system trap).

---

## 2. The pedagogy: lab -> lecture, with five lenses

Each unit is two artifacts:

- **`lab.md`** - the learner *does* something in a real cluster (CPU-only) and observes results, including
 deliberately *breaking* it to see the failure mode.
- **`lecture.md`** - the *course* built on what they just did. It explains the mechanism under the hood
 and then re-examines the topic from **five senior lenses**:
 1. **Dev** - what an application developer must know / change in code.
 2. **DevOps / Platform** - how it's operated, the tooling, the day-2 concerns.
 3. **Architect** - the trade-offs, when to use vs avoid, alternatives.
 4. **SRE** - failure modes, SLOs, toil, what pages you at 3am, how to debug.
 5. **AI/ML** - how the pattern maps to LLM/ML inference serving (conceptual; vLLM, KServe, GPU
     scheduling, KV-cache/VRAM, TTFT/TPOT, DCGM). **Never required to run anything GPU.**

Lecture tone: senior, concrete, opinionated. Prefer "here's the failure you'll actually hit and why"
over textbook restatement. Always end with **Pitfalls** and **Further reading** (book chapter).

---

## 3. Output layout & conventions

```
/home/talium/LEARN/kubernetes_book/COURSE/
  PLAN.md                        # this file (the build spec)
  README.md                      # learner-facing syllabus + how to use + cluster prereqs
  coverage-matrix.md             # 20 labs × (K8s objects · book chapters · SRE pillars)
  00-cluster-setup/
    eks.md                       # provision EKS + add-ons (concrete commands)
    ovh.md                       # provision OVH MKS + add-ons
    tooling.md                   # kubectl/helm/kubens/stern/k9s/fortio + demo images
    manifests/                   # shared bootstrap (namespaces, demo app images, helm values)
  NN-<slug>/                     # one folder per lab, NN = 01..20
    lab.md
    lecture.md
    manifests/                   # all YAML the lab applies
```

**Conventions (apply everywhere):**
- Namespace per lab: `lab-NN-<slug>` (e.g. `lab-02-deployments`). First step creates it; `cleanup`
 deletes it. Never deploy to `default`.
- Pin image tags (no `:latest`). Record exact tags in the lab.
- Every manifest sets `resources.requests` (and limits where the lab is about limits) so labs are
 good-citizen and schedulable on a 2-3 node cluster.
- Label everything: `app.kubernetes.io/name`, `app.kubernetes.io/part-of: k8s-sre-course`,
 `course.lab: "NN"`.
- Each `lab.md` Verify step must produce an **observable signal** (a command + expected output), not
 "it should work."
- **Cost guard:** call out any LoadBalancer / EBS / EFS / NAT resource a lab creates and put its teardown
 in Cleanup. No GPU requests anywhere.
- Keep cloud-specifics in clearly marked `### EKS` / `### OVH` blocks so the generic body stays portable.

---

## 4. Reusable demo apps (build once, reuse across labs)

To avoid bespoke images per lab, standardize on a tiny configurable HTTP app plus a few off-the-shelf
images. Prefer off-the-shelf to keep the course buildless where possible.

4.1 `course-app` (the one custom app - optional but recommended). A ~60-line Go or Python
(FastAPI/Flask) HTTP server, single small image. Behavior driven by env vars so one image serves many labs:
- `GET /` -> echo: returns hostname, pod name (from `POD_NAME` via downward API), version (`APP_VERSION` env).
- `GET /healthz` -> liveness; returns 500 after `FAIL_AFTER` requests if set (to demo CrashLoop).
- `GET /readyz` -> readiness; returns 503 until `READY_AFTER_SECONDS` elapsed (to demo slow start).
- `GET /metrics` -> Prometheus text (request count, in-flight gauge, latency histogram) - observability/adapter labs.
- `STARTUP_DELAY_SECONDS` -> sleep before serving (demo startupProbe).
- Reads a config file from `CONFIG_FILE` and a value from env `GREETING` (demo ConfigMap env vs file).
- Handles `SIGTERM`: stop accepting, drain in-flight, exit 0 within grace (demo managed lifecycle).
- `/work?ms=N` -> CPU burn (demo HPA load + resource limits).
If building is undesirable, use the off-the-shelf fallbacks below per behavior.

4.2 Off-the-shelf images (no build needed):
- `registry.k8s.io/e2e-test-images/agnhost:2.47` - official Swiss-army test image: `netexec` (HTTP echo,
 liveness/readiness toggling, shutdown), `serve-hostname`. **Primary fallback for course-app.**
- `kennethreitz/httpbin` - rich HTTP behaviors (status codes, delay) for probes/ingress.
- `nginxdemos/nginx-hello:plain-text` or `hashicorp/http-echo` - trivial echo backend.
- `polinux/stress` / `progrium/stress` - memory/CPU pressure for OOM/throttle/QoS labs.
- `redis:7-alpine`, `memcached:1.6-alpine` - stateful / sharded-cache labs.
- `busybox:1.36` / `alpine:3.20` - init containers, sidecars, debug shells, wget loops.
- `fortio/fortio` or `williamyeh/hey` - HTTP load generation (HPA, caching, scatter-gather).
- `ghcr.io/stefanprodan/podinfo` - production-like demo app with `/metrics`, health, versioned UI; great
 for deployment/canary/observability labs.
- `bitnami/kubectl` - in-cluster kubectl for leader-election / controller / work-queue manager labs.

4.3 Helm charts used (pin versions in `tooling.md`): `ingress-nginx`, `cert-manager`,
`kube-prometheus-stack` (Prometheus+Grafana+Alertmanager), `metrics-server` (if not an add-on),
optionally `keda`, `external-secrets`, `sealed-secrets`.

---

## 5. Cluster setup (lab 00) - concrete

Write `00-cluster-setup/eks.md`, `ovh.md`, `tooling.md`. The course assumes a **3-node, multi-AZ** cluster
(small instances), node-pool autoscaling enabled (min 2 / max 5), and the add-ons below.

### 5.1 EKS (`eks.md`)
- **Provision:** `eksctl create cluster` (or Terraform `terraform-aws-modules/eks`). 3× `t3.large`
 over 3 AZs, managed node group `min=2 max=5`. Enable OIDC provider (`eksctl utils
 associate-iam-oidc-provider`) - required for IRSA.
- **Add-ons (managed where possible):**
 - **EBS CSI** (managed add-on) -> `gp3` StorageClass (default), RWO.
 - **EFS CSI** (Helm/add-on) + EFS filesystem + StorageClass -> RWX (lab 09 RWX demo).
 - **metrics-server** (managed add-on) -> HPA + `kubectl top` (labs 05, 18).
 - **AWS Load Balancer Controller** (Helm, IRSA) -> `Service type=LoadBalancer` (NLB) + ALB Ingress (labs 06, 07).
 - **ingress-nginx** (Helm) -> portable Ingress (preferred over ALB so manifests match OVH).
 - **cert-manager** (Helm) -> TLS (lab 07).
 - **Cluster Autoscaler** (Helm, autodiscovery tags) **or Karpenter** -> node scale-out (lab 18). Default to Cluster Autoscaler for OVH parity.
 - **kube-prometheus-stack** (Helm) -> labs 13, 20.
 - **Network policy:** enable VPC CNI network policy (`enableNetworkPolicy=true`) or install Calico (lab 08).
- **Identity:** IRSA / EKS Pod Identity (lab 19 cloud-access demo).

### 5.2 OVH Managed Kubernetes (`ovh.md`)
- **Provision:** OVH Manager UI, Terraform (`ovh` + `openstack` providers), or API. One node pool,
 flavor ~`b2-7`, **autoscaling min=2 max=5** (OVH manages cluster-autoscaler for the pool).
- **Equivalents:**
 - **Storage: Cinder CSI StorageClasses `csi-cinder-classic` / `csi-cinder-high-speed` (RWO). RWX
    caveat:** no native RWX - for lab 09 RWX, deploy `nfs-subdir-external-provisioner` or OVH NAS-HA; document the limitation.
 - **LoadBalancer:** OVH Load Balancer via cloud-controller; annotations `service.beta.kubernetes.io/ovh-loadbalancer-*`. Public IP (cost - tear down).
 - **metrics-server:** usually preinstalled on MKS; else Helm.
 - **Autoscaler:** node-pool min/max (managed). No Karpenter equivalent.
 - **Ingress + cert-manager:** same Helm charts as EKS (portable).
 - **Network policy:** MKS CNI is Cilium/Canal depending on version - verify NetworkPolicy support; install Calico/Cilium if needed (lab 08).
 - **Identity:** **no IAM-per-pod.** For lab 19 cloud-access use External Secrets Operator against a vault, or `sealed-secrets`. Document the gap vs IRSA explicitly.
- **Tooling:** `kubectl`, `helm`, Manager kubeconfig, `kubens/kubectx`, `stern`, `k9s`, `fortio`/`hey`, `jq`, `yq`.

---

## 6. Templates (use verbatim section order)

### 6.1 `lab.md` template
```
# Lab NN - <Title>
**Patterns:** <book pattern tags>   **Source:** KIA <ch>, KP <ch>, DDS <ch>   **Est:** <min>

## Objective         # 1-2 lines: the capability gained
## Concepts exercised # bullet list of K8s objects/mechanisms touched
## Prerequisites      # prior labs + cluster add-ons needed
## Setup              # create namespace lab-NN-<slug>; apply base manifests
## Steps              # numbered; each step = command(s) + what to observe
   ### EKS            # cloud-specific commands/annotations
   ### OVH            # cloud-specific equivalents
## Verify             # observable success signal(s): command -> expected output
## Break it           # induce the failure mode the lecture explains; observe symptoms
## Cleanup            # kubectl delete ns lab-NN-<slug>; remove any cloud LB/volume
```

### 6.2 `lecture.md` template
```
# Lecture NN - <Title>
## What just happened (under the hood)   # mechanism: controllers, kube-proxy, scheduler, etc.
## Dev notes
## DevOps / Platform notes
## Architect notes (trade-offs)
## SRE notes (failure modes, SLOs, toil)
## AI/ML notes (LLM/ML serving mapping - conceptual)
## Pitfalls
## Further reading   # cite book chapters by name
```

---

## 7. The 20 labs - full per-lab spec

> Each block is enough to write `lab.md` + `manifests/` + `lecture.md`. Step lists are outlines (turn
> each bullet into concrete commands/manifests). Lens bullets are the lecture talking points.

### Phase A - Foundations & core workloads

#### 01 - Cluster fundamentals & kubectl fluency
- **Source:** KIA 2-3.
- **Objective:** Operate the cluster confidently; understand the declarative model and label-driven selection.
- **Concepts:** kubeconfig/contexts, namespaces, Pod (imperative `run` + YAML), labels/annotations,
 selectors, `get/describe/logs/exec/events`, `kubectl debug` (ephemeral container), `--dry-run=client -o yaml`, `kubectl explain`.
- **Manifests:** a Pod (course-app/agnhost) with labels; a second Pod with different labels.
- **Steps:** create ns -> run pod imperatively -> regenerate YAML via dry-run -> apply -> label & select ->
 describe/logs/exec -> break image name to see `ImagePullBackOff` in events -> `kubectl debug` into it.
- **Verify:** `kubectl get pods -l app=demo` Running; `describe` shows events.
- **Break it:** bad image tag -> `ErrImagePull`/`ImagePullBackOff`.
- **Cleanup:** delete ns.
- **Lecture - under the hood:** kubectl -> API server (authn/authz/admission) -> etcd -> controllers ->
 scheduler -> kubelet; everything reconciles to desired state; labels are the universal join key.
 - *Dev:* label/annotation discipline; annotations for non-identifying metadata.
 - *DevOps:* contexts per cluster, RBAC-scoped kubeconfigs, namespaces as soft tenancy.
 - *Architect:* declarative reconciliation vs imperative drift.
 - *SRE:* `describe` + events is the first debugging move; learn Pod phases & reasons.
 - *AI/ML:* namespace-per-model/team; labels to select model versions for canary routing.
 - *Pitfalls:* editing live objects (drift); `latest` tags. *Reading:* KIA ch3.

#### 02 - Deployments, ReplicaSets, rolling updates & rollback *(Declarative Deployment)*
- **Source:** KIA 9, KP 3.
- **Objective:** Ship and safely update a replicated stateless service with zero downtime; roll back.
- **Concepts:** Deployment, ReplicaSet (revision hash), `strategy.RollingUpdate` (`maxSurge`,
 `maxUnavailable`), `minReadySeconds`, `kubectl rollout status/history/undo`, `revisionHistoryLimit`.
- **Manifests:** Deployment (podinfo/course-app) v1 with readiness probe; v2; a broken v3.
- **Steps:** deploy v1 (3 replicas) -> inspect ReplicaSet -> `set image` v2 -> watch surge/rolling ->
 `rollout history` -> push broken v3 (bad readiness) -> rollout stalls -> `rollout undo`.
- **Verify:** during update a `fortio`/`hey` loop shows ~0 failed requests; `rollout status` succeeds.
- **Break it:** v3 with always-failing readiness -> rollout never completes (surge stalls).
- **Lecture:** ReplicaSet pod-template-hash, RollingUpdate vs Recreate, why readiness gates the rollout.
 - *Dev:* immutable image tags; backward-compatible schema for zero-downtime.
 - *DevOps:* GitOps (Argo CD/Flux) for drift; progressive delivery (Argo Rollouts/Flagger) for canary/blue-green.
 - *Architect:* deployment strategies & their blast radius.
 - *SRE:* automated rollback on SLO/error-budget breach; bake-time via `minReadySeconds`.
 - *AI/ML:* roll a new model version with warm-up/readiness so cold replicas don't serve; canary by traffic %.
 - *Pitfalls:* no readiness probe -> rollout "succeeds" into a brownout. *Reading:* KP "Declarative Deployment", KIA ch9.

#### 03 - Health probes & graceful lifecycle *(Health Probe + Managed Lifecycle)*
- **Source:** KIA 4,5,17; KP 4,5; DDS health.
- **Objective:** Distinguish liveness/readiness/startup; achieve graceful shutdown with no dropped requests.
- **Concepts:** `livenessProbe`/`readinessProbe`/`startupProbe` (httpGet/exec/tcp/gRPC), `initialDelaySeconds`,
 `periodSeconds`, `failureThreshold`, `preStop` hook, `terminationGracePeriodSeconds`, SIGTERM->SIGKILL.
- **Manifests:** course-app with all three probes; `READY_AFTER_SECONDS`, `STARTUP_DELAY_SECONDS`, `FAIL_AFTER`; `preStop` sleep.
- **Steps:** deploy slow-start -> startupProbe holds liveness off -> flip readiness false -> endpoint removed
 (traffic stops) while pod stays up -> set `FAIL_AFTER` -> liveness restarts -> add `preStop`+grace -> rolling update under load shows zero 5xx.
- **Verify:** `fortio` during rollout: 0 errors; `kubectl get endpoints` drops a NotReady pod.
- **Break it:** aggressive liveness on slow app -> CrashLoopBackOff thrash.
- **Lecture:** the three probes are independent; SIGTERM contract; connection draining order.
 - *Dev:* honest `/healthz` (don't check downstreams in liveness); readiness = "can serve now".
 - *DevOps:* probe defaults that thrash; tune `failureThreshold`/`periodSeconds`.
 - *Architect:* cascading-restart anti-pattern; liveness ≠ deep dependency check.
 - *SRE:* graceful shutdown as an availability lever; probe tuning as toil reducer.
 - *AI/ML:* huge model load -> long `startupProbe`; drain in-flight inference before exit; readiness false while loading weights.
 - *Pitfalls:* liveness calling the DB; grace period < drain time. *Reading:* KP "Health Probe"/"Managed Lifecycle", KIA ch4/17.

#### 04 - Configuration & secrets *(EnvVar / Config Resource / Immutable / Template)*
- **Source:** KIA 7; KP 19-22.
- **Objective:** Externalize config; understand env-vs-file behavior and secret handling.
- **Concepts:** ConfigMap (env `valueFrom.configMapKeyRef`, `envFrom`, volume mount), Secret (volume/env),
 `immutable: true`, Kustomize overlay (dev/prod), file hot-reload vs env-needs-restart.
- **Manifests:** ConfigMap (greeting + config file), Secret, course-app reading both; Kustomize `base/` + `overlays/dev|prod`.
- **Steps:** mount ConfigMap as env and file -> change ConfigMap -> env unchanged on running pod but mounted
 file updates (after kubelet sync) -> mark immutable -> edit rejected -> Kustomize build dev vs prod.
- **Verify:** `kubectl exec ... cat /etc/config/...` reflects update; env still old until restart.
- **Break it:** put a secret in an env var -> `kubectl describe pod` reveals it in plain text.
- **Lecture:** config-from-image separation; 1 MB limit; env immutable at start; Base64 ≠ encryption.
 - *Dev:* 12-factor config; reload-on-file-change.
 - *DevOps:* Kustomize/Helm; etcd encryption-at-rest; never commit plaintext secrets.
 - *Architect:* config-as-code vs templating (Immutable/Template) trade-offs.
 - *SRE:* config change = top incident cause; treat as a deploy with rollback.
 - *AI/ML:* model version/config pinning; prompt templates & sampling params as ConfigMap; large config hitting 1 MB -> Immutable Config image.
 - *Pitfalls:* secrets in env; expecting live env updates. *Reading:* KP Part IV, KIA ch7.

#### 05 - Resource requests/limits, QoS & quotas *(Predictable Demands)*
- **Source:** KIA 14; KP 2.
- **Objective:** Make scheduling predictable; understand QoS and namespace guardrails.
- **Concepts:** `requests`/`limits` (CPU millicores, memory), QoS (Guaranteed/Burstable/BestEffort),
 OOMKill, CPU throttling, `LimitRange`, `ResourceQuota`.
- **Manifests:** three pods (one per QoS class) using `stress`/`/work`; LimitRange; ResourceQuota.
- **Steps:** deploy three classes -> `describe` shows QoS -> drive memory over limit -> OOMKilled -> drive CPU
 to limit -> throttling (no kill) -> apply LimitRange (auto-defaults) -> apply ResourceQuota -> exceed -> rejected.
- **Verify:** `kubectl get pod -o jsonpath='{.status.qosClass}'`; OOMKilled in `describe`; quota rejection.
- **Break it:** BestEffort pod evicted first under node memory pressure (simulate with stress).
- **Lecture: compressible (CPU, throttled) vs incompressible (memory, OOM); scheduler uses requests**;
 eviction order = BestEffort -> Burstable -> Guaranteed.
 - *Dev:* right-size from observed usage.
 - *DevOps:* LimitRange/Quota as multi-tenant guardrails.
 - *Architect:* bin-packing density vs isolation; whether to set CPU limits at all.
 - *SRE:* OOM/throttle as hidden latency; capacity planning; PriorityClass & preemption.
 - *AI/ML:* GPU = non-overcommittable extended resource (`nvidia.com/gpu`, integer); VRAM ≈ hard memory; KV-cache sizing vs GPU OOM.
 - *Pitfalls:* BestEffort in prod; memory limit < working set. *Reading:* KP "Predictable Demands", KIA ch14.

### Phase B - Networking & service discovery

#### 06 - Services, Endpoints & cluster DNS *(Service Discovery)*
- **Source:** KIA 5; KP 13; DDS 6.
- **Objective:** Expose and discover workloads internally and externally.
- **Concepts:** ClusterIP, NodePort, LoadBalancer, headless (`clusterIP: None`), Endpoints/EndpointSlice,
 CoreDNS names, readiness gating endpoints, kube-proxy.
- **Manifests:** Deployment + ClusterIP svc; NodePort svc; LoadBalancer svc; headless svc.
- **Steps:** resolve service DNS from a client pod -> scale backend, watch EndpointSlice -> kill a pod,
 endpoint removed -> create LoadBalancer (cloud LB) -> headless returns per-pod A records.
 - **EKS:** LoadBalancer -> NLB via AWS LB Controller (internal/external annotations).
 - **OVH:** LoadBalancer -> OVH LB via cloud-controller; `ovh-loadbalancer` annotations; public IP cost.
- **Verify:** `nslookup`/`wget` to ClusterIP DNS works; `kubectl get endpointslices` updates on scale.
- **Break it:** readiness false on all pods -> empty endpoints -> connection refused.
- **Lecture:** kube-proxy iptables/IPVS DNAT; VIP vs headless; readiness controls membership.
 - *Dev:* depend on DNS names, not IPs.
 - *DevOps:* LB lifecycle/cost; EndpointSlices vs legacy Endpoints at scale.
 - *Architect:* east-west (ClusterIP) vs north-south (LB/Ingress).
 - *SRE:* empty-endpoints = "service down but pods up"; check readiness + selector.
 - *AI/ML:* model service discovery; headless for replica-addressable/sharded inference servers.
 - *Pitfalls:* selector/label mismatch -> silent empty endpoints. *Reading:* KP "Service Discovery", KIA ch5.

#### 07 - Ingress, TLS & replicated load-balanced serving *(Replicated Load-Balanced Service)*
- **Source:** KIA 5; DDS 6.
- **Objective:** L7 routing + TLS; add a caching tier in front of a replicated service.
- **Concepts:** Ingress (host/path), IngressClass, ingress-nginx, cert-manager (issuer/TLS secret),
 multi-tier (edge -> cache -> app), session affinity.
- **Manifests:** two backends (podinfo v1/v2), Ingress path routing, TLS secret via cert-manager, an nginx/Varnish cache in front.
- **Steps:** install ingress-nginx (setup) -> route `/a`->A `/b`->B -> add TLS -> curl https -> add cache ->
 `hey` load shows cache hits reduce backend RPS.
 - **EKS:** ingress-nginx (portable) preferred; note ALB Ingress alternative.
 - **OVH:** ingress-nginx identical; OVH LB in front.
- **Verify:** `curl -k https://host/a` -> A; cache hit ratio in cache logs/metrics.
- **Break it:** remove cache, push load -> backend saturates (latency climbs) -> "caching not optional".
- **Lecture:** L7 routing, TLS at edge, caching becomes mandatory under load, sticky-session pitfalls across tiers.
 - *Dev:* idempotent handlers behind LB; cache-control headers.
 - *DevOps:* cert rotation; Gateway API as Ingress successor.
 - *Architect:* multi-tier topology; where to terminate TLS.
 - *SRE:* edge rate-limiting prevents downstream cascades; tail latency at the edge.
 - *AI/ML:* response/semantic caching in front of LLM endpoints (TTFT win); gateway for token-based routing.
 - *Pitfalls:* terminating TLS everywhere; cache stampede. *Reading:* DDS "Replicated Load-Balanced Services", KIA ch5.

#### 08 - NetworkPolicy & segmentation *(Network Segmentation)*
- **Source:** KIA 13; KP 24.
- **Objective:** Restrict pod-to-pod traffic to least connectivity.
- **Concepts:** default-allow flat network, `NetworkPolicy` (podSelector, namespaceSelector, ingress/egress),
 default-deny, CNI dependence (Calico/Cilium), L3/L4 vs L7 (mesh/mTLS).
- **Manifests:** frontend->backend->db tiers; default-deny; allow-lists per hop; egress restriction.
- **Steps:** confirm all tiers talk (no policy) -> default-deny (all breaks) -> allow frontend->backend,
 backend->db -> prove frontend can't reach db -> restrict egress (block external).
 - **EKS:** enable VPC CNI network policy or install Calico.
 - **OVH:** verify CNI supports NetworkPolicy; install Calico/Cilium if needed.
- **Verify:** `kubectl exec frontend -- wget backend` ok; `wget db` times out.
- **Break it:** forget egress DNS allow -> pods can't resolve names - show & fix.
- **Lecture:** default is allow-all; policies are additive allow-lists; mesh adds L7 + mTLS.
 - *Dev:* declare required connectivity.
 - *DevOps:* CNI choice gates the feature; policy testing.
 - *Architect:* zero-trust, blast-radius containment.
 - *SRE:* segmentation vs debuggability; the DNS egress gotcha.
 - *AI/ML:* isolate model/data planes; egress control to prevent weight/data exfiltration; mTLS between inference services.
 - *Pitfalls:* default-deny without DNS egress; CNI doesn't enforce policy. *Reading:* KP "Network Segmentation", KIA ch13.

### Phase C - Storage & stateful

#### 09 - Volumes, PV/PVC & dynamic provisioning
- **Source:** KIA 6; KP config.
- **Objective:** Persist data beyond pod lifetime; understand provisioning & access modes.
- **Concepts:** emptyDir (shared), PVC + dynamic StorageClass, PV binding, reclaim policy (Delete/Retain),
 access modes (RWO/ROX/RWX), volume expansion, snapshots.
- **Manifests:** two-container pod sharing emptyDir; PVC (RWO) + pod writing data; RWX PVC.
- **Steps:** emptyDir share -> PVC dynamic provision (cloud disk) -> write file -> delete pod -> recreate ->
 data persists -> expand PVC -> (RWX) two pods mount same volume.
 - **EKS:** `gp3` (EBS CSI, RWO); EFS CSI for RWX.
 - **OVH:** `csi-cinder-high-speed` (RWO); **RWX caveat** -> NFS provisioner / OVH NAS-HA.
- **Verify:** data survives pod deletion; `kubectl get pv` Bound; expansion reflected.
- **Break it:** reclaim Delete -> delete PVC -> PV+cloud disk gone (data loss) - contrast with Retain.
- **Lecture:** PV/PVC/StorageClass separation; RWO vs RWX backends; reclaim semantics.
 - *Dev:* prefer stateless; local disk is ephemeral.
 - *DevOps:* CSI, snapshots, expansion, backup (Velero).
 - *Architect:* where state lives (in-cluster vs managed DB).
 - *SRE:* storage failure modes; backup/restore drills; orphaned PVs.
 - *AI/ML:* model-weights volumes (RWX shared read); dataset volumes; `emptyDir.medium: Memory` for KV-cache scratch.
 - *Pitfalls:* assuming RWX everywhere; Delete reclaim eating data. *Reading:* KIA ch6.

#### 10 - StatefulSets *(Stateful Service)*
- **Source:** KIA 10; KP 12.
- **Objective:** Run apps needing stable identity, storage, and ordering.
- **Concepts:** StatefulSet, `volumeClaimTemplates`, headless Service, ordinal names (`x-0..n`),
 stable DNS, ordered start/stop, partitioned rolling update, scale-down keeps PVCs.
- **Manifests:** redis/fake-DB StatefulSet (3) + headless svc; partitioned update.
- **Steps:** deploy -> ordered creation `0->1->2` -> each gets own PVC -> resolve `pod-0.svc` DNS -> scale down
 (PVCs retained) -> partitioned update canaries highest ordinal first.
- **Verify:** `kubectl get pvc` one per ordinal; `x-0.svc.ns` resolves to that pod.
- **Break it:** delete `x-1` -> recreated with same name + same PVC (identity preserved).
- **Lecture:** "at most one" per ordinal; ordinality for sequenced ops; scale-down data-safety choice.
 - *Dev:* peer discovery via stable DNS; clustering logic in the app.
 - *DevOps:* operating clustered stores is hard - prefer managed DB unless necessary.
 - *Architect:* StatefulSet vs external managed datastore.
 - *SRE:* ordered failover; quorum & split-brain; backup per-pod volumes.
 - *AI/ML:* sharded model servers / distributed KV cache needing stable identity & pinned storage.
 - *Pitfalls:* expecting HA for free; PVCs lingering after scale-down. *Reading:* KP "Stateful Service", KIA ch10.

#### 11 - Singleton, PodDisruptionBudget & leader election *(Singleton Service + Ownership Election)*
- **Source:** KP 10; DDS 10; KIA 4.
- **Objective:** Run "at-most one" reliably; survive maintenance gracefully.
- **Concepts:** single-replica patterns (at-least-once vs at-most-once), `PodDisruptionBudget`,
 leader election via `coordination.k8s.io/Lease`, node drain/cordon, voluntary vs involuntary disruption.
- **Manifests:** 3-replica leader-election app (bitnami/kubectl + lease, or Go leaderelection sample); PDB; a web Deployment + PDB.
- **Steps:** deploy 3 candidates -> one acquires Lease (leader) -> kill leader -> new leader within TTL ->
 add PDB -> `kubectl drain` a node -> PDB blocks eviction below threshold.
- **Verify:** only one leader logs "I am leader"; `kubectl drain` respects PDB.
- **Break it:** PDB `minAvailable: 100%` -> drain hangs forever (over-constrained).
- **Lecture:** at-least-once (RS=1, brief overlap) vs at-most-once (StatefulSet/lease); leases = time-bounded ownership; PDB bounds maintenance impact.
 - *Dev:* idempotent leader work; renew lease, handle loss.
 - *DevOps:* safe drains/upgrades depend on PDBs.
 - *Architect:* active-passive coordination; avoid hard-coded singletons.
 - *SRE:* node maintenance without outage; cluster upgrades.
 - *AI/ML:* single coordinator for batch-inference/training-job orchestration; lease-based scheduler ownership.
 - *Pitfalls:* over-tight PDB stalls upgrades; replicas=1 ≠ true singleton. *Reading:* DDS "Ownership Election", KP "Singleton Service".

### Phase D - Composition patterns (multi-container)

#### 12 - Init containers & Sidecar *(Init Container + Sidecar)*
- **Source:** KIA 17; KP 15,16; DDS 3.
- **Objective:** Compose pods: ordered setup + concurrent helper.
- **Concepts:** `initContainers` (ordered, run-to-completion), native sidecar (initContainer with
 `restartPolicy: Always`, 1.28+), shared `emptyDir`, localhost IPC, startup ordering.
- **Manifests:** init container (busybox seed into emptyDir) + app serving it; sidecar (log tailer/content-sync) sharing the volume.
- **Steps:** init populates volume before app -> break init (exit 1) -> pod stuck Init -> fix -> add sidecar
 keeping content fresh -> kill sidecar, app keeps serving (until native sidecar restarts it).
- **Verify:** app serves init-provided content; sidecar logs show periodic sync.
- **Break it:** init container fails -> `Init:Error`/`Init:CrashLoopBackOff`, app never starts.
- **Lecture:** init = guaranteed order; classic sidecars = concurrent, no ordering; native sidecars fix lifecycle.
 - *Dev:* separation of concerns; don't bake helpers into the app image.
 - *DevOps:* mesh sidecar injection (Istio/Linkerd); sidecar sprawl.
 - *Architect:* composition over modification; reusable helper images.
 - *SRE:* sidecar resource/latency tax; sidecar crash semantics.
 - *AI/ML:* model-puller init container (download weights to emptyDir); telemetry sidecar beside an inference server (vLLM + exporter).
 - *Pitfalls:* classic sidecar that must outlive app (use native sidecar); init downloading on every restart. *Reading:* KP "Init Container"/"Sidecar", DDS ch3.

#### 13 - Ambassador & Adapter *(Ambassador + Adapter)*
- **Source:** KP 17,18; DDS 4,5.
- **Objective:** Decouple app from network topology (ambassador) and normalize its interface (adapter).
- **Concepts:** ambassador proxy sidecar (app -> localhost -> ambassador -> external/shards), adapter sidecar (native metrics/logs -> standard Prometheus `/metrics`).
- **Manifests:** app talking to `localhost:6379`; ambassador (envoy/nginx/redis-proxy) routing to a backend or sharded redis; adapter sidecar exposing Prometheus metrics.
- **Steps:** app connects to localhost -> ambassador routes to backend A -> swap ambassador config to B
 (app unchanged) -> add adapter -> Prometheus scrapes normalized `/metrics`.
- **Verify:** changing ambassador target reroutes with no app change; `/metrics` returns Prom format.
- **Break it:** kill ambassador -> app loses connectivity (shows the coupling point).
- **Lecture:** ambassador hides topology/discovery/retries/TLS; adapter unifies operational interface.
 - *Dev:* localhost contract; app stays topology-agnostic.
 - *DevOps:* polyglot fleets with uniform telemetry; one scrape format.
 - *Architect:* ambassador vs full service mesh (Envoy).
 - *SRE:* standardized `/metrics` everywhere is an observability prerequisite.
 - *AI/ML:* ambassador routing to model shards by consistent-hash; adapter normalizing Triton/vLLM metrics -> Prometheus.
 - *Pitfalls:* ambassador as SPOF; adapter doing heavy transformation. *Reading:* KP "Ambassador"/"Adapter", DDS ch4/5.

### Phase E - Scheduling & node-level

#### 14 - Scheduling: affinity, anti-affinity, topology spread, taints/tolerations *(Automated Placement)*
- **Source:** KIA 16; KP 6.
- **Objective:** Control where pods land for HA and isolation.
- **Concepts:** `nodeSelector`, `nodeAffinity` (required/preferred), `podAffinity`/`podAntiAffinity`
 (topologyKey), `topologySpreadConstraints` (AZ), taints + tolerations, descheduler (concept).
- **Manifests:** Deployment with anti-affinity (spread across nodes), topology spread across AZ, tainted node + toleration, nodeAffinity to a labeled pool.
- **Steps:** label nodes -> nodeAffinity pins -> anti-affinity spreads 1/node -> topology spread balances AZ ->
 taint a node -> pod without toleration avoids it -> add toleration -> lands.
 - **EKS:** AZ via `topology.kubernetes.io/zone`; dedicated managed node groups; taints on GPU pools.
 - **OVH:** node pools/flavors; label & taint pools; anti-affinity for spread.
- **Verify:** `kubectl get pods -o wide` one replica per node / balanced per AZ.
- **Break it:** over-constrain (anti-affinity + too few nodes) -> pods Pending Unschedulable.
- **Lecture:** scheduler filter->score; hard vs soft; prefer topologySpread over anti-affinity; descheduler rebalances.
 - *Dev:* declare placement intent, not node names.
 - *DevOps:* dedicated node pools (taints) for special hardware/tenants.
 - *Architect:* fault domains; survive AZ loss.
 - *SRE:* spread to survive a zone outage; watch for Unschedulable pending.
 - *AI/ML:* GPU node pools tainted `nvidia.com/gpu:NoSchedule` + tolerations; gang/bin-pack for multi-GPU TP/PP; topology for NVLink locality (conceptual).
 - *Pitfalls:* unschedulable from too many constraints; affinity assuming stable labels. *Reading:* KP "Automated Placement", KIA ch16.

#### 15 - DaemonSet & node agents *(Daemon Service)*
- **Source:** KIA 4; KP 9; DDS adapter.
- **Objective:** Run one pod per node for node-level concerns.
- **Concepts:** DaemonSet (one-per-node), nodeSelector/affinity to subset, tolerations (tainted nodes),
 rolling DS update, priority.
- **Manifests:** a log/metrics agent DaemonSet (fluent-bit / node-exporter / busybox stand-in) on all nodes; a subset variant.
- **Steps:** deploy DaemonSet -> one pod per node -> add node -> pod auto-added -> restrict to subset via
 selector -> tolerate a taint to cover tainted nodes.
- **Verify:** `kubectl get ds` desired==current==ready==#nodes; `-o wide` one per node.
- **Break it:** nodeSelector matching nothing -> 0 scheduled.
- **Lecture:** DaemonSet uses the scheduler (1.17+) so affinity/preemption apply; infra vs app layer.
 - *Dev:* node-local concerns don't belong in app pods.
 - *DevOps:* fluent-bit/node-exporter/CSI/CNI as DaemonSets; rolling DS updates.
 - *Architect:* platform layer vs application layer.
 - *SRE:* node-level observability coverage; agents must tolerate all taints or you get blind nodes.
 - *AI/ML:* `nvidia-device-plugin` + `dcgm-exporter` as DaemonSets for GPU telemetry (concept only - no GPU in lab).
 - *Pitfalls:* DS missing tolerations -> blind on tainted nodes. *Reading:* KP "Daemon Service", KIA ch4.

### Phase F - Batch & async

#### 16 - Jobs, CronJobs & work queue *(Batch Job + Periodic Job + Work Queue)*
- **Source:** KIA 4; KP 7,8; DDS 11.
- **Objective:** Run finite and scheduled work; build a simple work queue.
- **Concepts:** Job (`completions`, `parallelism`, `backoffLimit`, `restartPolicy`), Indexed Job
 (`completionMode: Indexed`, `JOB_COMPLETION_INDEX`), CronJob (`schedule`, `concurrencyPolicy`,
 `startingDeadlineSeconds`, `ttlSecondsAfterFinished`, history limits), work-queue (manager + worker Jobs).
- **Manifests:** parallel Job; Indexed Job sharding items by index; CronJob with `Forbid`; manager pod (bitnami/kubectl) creating worker Jobs from a ConfigMap list.
- **Steps:** parallel Job (parallelism=3, completions=6) -> Indexed Job each pod its slice -> CronJob every
 minute -> slow run + `Forbid` -> next run skipped -> TTL auto-cleans -> manager spawns one Job per item.
- **Verify:** Job `Complete`; indexed pods log distinct indices; CronJob skips overlapping run.
- **Break it:** worker always fails -> `backoffLimit` exhausted -> Job `Failed`; obsolete-work pileup discussion.
- **Lecture:** at-least-once semantics (duplication possible); parallelism vs completions; indexed sharding.
 - *Dev:* idempotent workers.
 - *DevOps:* Job/CronJob history cleanup; TTL controller.
 - *Architect:* reusable queue source + worker interface (DDS work-queue).
 - *SRE:* stuck/duplicated jobs; "processing obsolete work" after an outage backlog; triage newest-first.
 - *AI/ML:* offline/batch inference & embedding generation as Indexed Jobs; nightly eval runs as CronJobs.
 - *Pitfalls:* non-idempotent workers; missing TTL -> Job pileup. *Reading:* KP "Batch Job"/"Periodic Job", DDS ch11.

#### 17 - Event-driven & coordinated batch *(Scatter/Gather + Event-Driven + Coordinated Batch)*
- **Source:** DDS 8,12,13.
- **Objective:** Compose async pipelines and parallel compute.
- **Concepts:** queue-depth-driven scaling (KEDA optional, else manual), scatter/gather (root fans out
 to leaves, aggregates), event-driven chain (stages via topics/queue), coordinated batch (map -> shuffle on shared volume -> reduce barrier).
- **Manifests:** root service querying N leaf services in parallel + merge; 2-stage Job pipeline
 (map parallelism=N -> reduce) over a shared PVC; optional KEDA ScaledObject on a queue.
- **Steps:** leaves with partitions -> root scatter/gather -> measure parallel vs sequential latency ->
 map/reduce word-count over shared volume -> (optional) KEDA scales workers by queue depth.
- **Verify:** scatter/gather faster than sequential; reduce output correct.
- **Break it:** one slow leaf dominates total latency (tail latency) -> add timeout/partial-result.
- **Lecture:** fan-out tail latency; pub/sub decoupling; fork/join barriers; dead-letter queues; backpressure.
 - *Dev:* partial-result handling; timeouts.
 - *DevOps:* Kafka/SQS + KEDA; topic lag monitoring.
 - *Architect:* batch vs streaming; when to introduce a broker.
 - *SRE:* topic-lag alerts; backpressure; one slow shard = slow everything.
 - *AI/ML:* scatter/gather over a sharded vector index (RAG retrieval); distributed embedding map-reduce; ensemble inference fan-out.
 - *Pitfalls:* no timeout on slowest leaf; unbounded queue growth. *Reading:* DDS ch8/12/13.

### Phase G - Scaling, security, reliability

#### 18 - Autoscaling: HPA, VPA, Cluster Autoscaler *(Elastic Scale)*
- **Source:** KIA 15; KP 29.
- **Objective:** Scale pods and nodes to load, safely.
- **Concepts:** HPA (CPU + custom metric via metrics-server/Prometheus Adapter), `behavior` (scale
 policies, stabilization), VPA (concept + recommend mode), Cluster Autoscaler / Karpenter, scale-to-zero
 (KEDA/Knative concept), PDB during scale-down.
- **Manifests:** Deployment + HPA (CPU target); `fortio` load generator; PDB; (optional) Prometheus-Adapter custom metric.
- **Steps:** apply HPA -> load -> replicas climb -> stop load -> stabilization -> scale down -> push beyond node
 capacity -> pods Pending -> Cluster Autoscaler adds a node -> load off -> node removed.
 - **EKS: Cluster Autoscaler (autodiscovery) and** Karpenter (document both).
 - **OVH:** node-pool autoscaler (min/max) - managed; no Karpenter.
- **Verify:** `kubectl get hpa` replicas track load; new node appears under sustained pressure.
- **Break it:** HPA target too low / no requests -> flapping or no scaling; metrics-lag oscillation.
- **Lecture:** HPA control loop & metrics lag; HPA+VPA conflict on the same resource; scale-to-zero for bursty/costly workloads.
 - *Dev:* design for horizontal scale (stateless, externalize state).
 - *DevOps:* autoscaler tuning, scale-down stabilization, cost.
 - *Architect:* reactive (HPA) vs predictive vs scheduled scaling.
 - *SRE:* scale-up latency vs SLO; thundering herd on cold start; PDB so scale-down/drain is safe.
 - *AI/ML:* autoscale inference on queue depth / GPU utilization (DCGM), not CPU; scale-to-zero matters for costly GPUs; KEDA on request queue; cold-start = model load time.
 - *Pitfalls:* HPA without requests; HPA+VPA fighting; oscillation. *Reading:* KP "Elastic Scale", KIA ch15.

#### 19 - RBAC, ServiceAccounts, Pod Security & secrets *(Access Control + Process Containment + Secure Configuration)*
- **Source:** KIA 12,13; KP 23,24,25,26.
- **Objective:** Least-privilege identity, hardened pods, real secret management.
- **Concepts:** ServiceAccount, Role/RoleBinding, ClusterRole/ClusterRoleBinding, verbs, `kubectl auth
 can-i`, `securityContext` (runAsNonRoot, drop ALL caps, readOnlyRootFilesystem, allowPrivilegeEscalation:false),
 Pod Security Standards (baseline/restricted) via PSA labels, secret management (External/Sealed Secrets).
- **Manifests:** SA + Role (get/list pods) + RoleBinding; a pod using the SA token to call the API;
 hardened securityContext pod; namespace labeled `pod-security.kubernetes.io/enforce: restricted`; External/Sealed Secret example.
- **Steps:** SA + minimal Role -> pod lists pods (ok) -> tries delete (forbidden) -> add verb -> harden
 securityContext -> deploy root/privileged pod into `restricted` namespace -> rejected by PSA -> fix ->
 manage a secret via Sealed/External Secrets instead of plaintext.
 - **EKS:** **IRSA / Pod Identity** to grant a pod scoped AWS (S3) access without static keys.
 - **OVH:** **no IAM-per-pod** -> External Secrets Operator against a vault, or Sealed Secrets. State the gap.
- **Verify:** `kubectl auth can-i delete pods --as=system:serviceaccount:...` -> no/yes; PSA rejects root pod.
- **Break it:** bind `cluster-admin` to a SA -> over-privilege; then remediate.
- **Lecture:** authn -> authz (RBAC) -> admission (PSA/OPA/Kyverno) chain; least privilege; Base64 ≠ encryption; root-in-container ≈ host risk.
 - *Dev:* run as non-root; drop caps; read-only FS.
 - *DevOps:* admission controllers (Kyverno/OPA Gatekeeper); etcd encryption-at-rest.
 - *Architect:* blast-radius isolation via per-workload SAs; secret rotation.
 - *SRE:* audit logs; review who-can-do-what; privilege-escalation paths (create/patch roles).
 - *AI/ML:* scope model-bucket/registry access per inference SA; protect model weights & API keys; tenant isolation for multi-model platforms.
 - *Pitfalls:* default SA over-mounted token; cluster-admin sprawl; secrets in env/Git. *Reading:* KP Part V (Security), KIA ch12/13.

#### 20 - Observability, SLOs, failure patterns & extending K8s *(Monitoring + Failure Patterns + Controller/Operator)* - capstone
- **Source:** DDS 14,16; KIA 18; KP 27,28.
- **Objective:** See the system, define reliability targets, handle failure, and extend K8s.
- **Concepts:** Prometheus scrape (`ServiceMonitor`), four pillars (logs/metrics/traces/alerts), RED & USE,
 SLI/SLO/error budget, Alertmanager rule (burn rate), OpenTelemetry trace across services, failure
 patterns (retries + exponential backoff + jitter, circuit breaker, "absence of errors is an error",
 processing obsolete work), CRD + minimal controller/operator (kubebuilder walkthrough or tiny reconcile loop).
- **Manifests:** kube-prometheus-stack values; `ServiceMonitor` for course-app `/metrics`; PrometheusRule
 (SLO burn-rate alert); OTel-instrumented 3-service chain; CRD (`Website`) + a controller that creates a Deployment+Service per CR.
- **Steps:** scrape metrics -> Grafana RED dashboard -> define SLO (99% < 300 ms) + burn-rate alert ->
 induce errors -> alert fires -> add retries+backoff+jitter / circuit breaker -> trace a request end-to-end ->
 apply CRD + controller -> create a `Website` CR -> controller materializes Deployment+Service (Operator pattern).
- **Verify:** Grafana shows RPS/latency/errors; alert fires on induced failure; creating a `Website` CR auto-creates its Deployment+Service.
- **Break it:** naive retries without backoff -> thundering herd amplifies an outage -> fix with backoff+jitter+breaker.
- **Lecture:** four pillars; RED/USE; SLI/SLO/error budgets as the SRE contract; common failure patterns;
 reconcile loop = how all of K8s works; operators encode operational knowledge.
 - *Dev:* instrument code (metrics + trace-context propagation); structured logs with request IDs.
 - *DevOps:* Prometheus/Grafana/Loki/Tempo + Alertmanager; ServiceMonitor/PodMonitor; dashboards as code.
 - *Architect:* observability as a system property; operators vs external automation.
 - *SRE:* **the capstone** - SLO-driven (burn-rate) alerting, error budgets, on-call hygiene, blameless incident debugging, toil reduction via operators.
 - *AI/ML:* inference SLOs (TTFT, TPOT, p99 latency, tokens/s, queue depth); GPU metrics via DCGM exporter; KServe/operators for model-serving lifecycle; model-quality & drift as extra SLIs.
 - *Pitfalls:* alerting on causes not symptoms; retries without backoff; logging everything (cost). *Reading:* DDS ch14/16, KP "Controller"/"Operator", KIA ch18.

---

## 8. `coverage-matrix.md` (build this to prove ~80%)

A table, one row per lab, columns: K8s objects · KIA ch · KP pattern · DDS pattern · SRE pillar.
Then a checklist asserting each item below is covered by ≥1 lab:

- **Workloads:** Pod, ReplicaSet, Deployment, StatefulSet, DaemonSet, Job, CronJob.
- **Networking:** Service (ClusterIP/NodePort/LB/headless), Endpoints/EndpointSlice, DNS, Ingress, NetworkPolicy.
- **Config/Storage:** ConfigMap, Secret, PV/PVC, StorageClass, CSI, volume types.
- **Scheduling:** requests/limits/QoS, LimitRange, ResourceQuota, affinity/anti-affinity, topology spread, taints/tolerations, PriorityClass.
- **Scaling/Resilience:** HPA, VPA, Cluster Autoscaler/Karpenter, PDB, leader election.
- **Security:** RBAC, ServiceAccount, securityContext, Pod Security Standards, secret management, IRSA/OVH identity.
- **Patterns:** all 6 KP parts; DDS single-node/serving/batch; observability + failure patterns.
- **Extending:** CRD, controller, operator.
- **SRE pillars:** observability (logs/metrics/traces), SLI/SLO/error budgets, change management, capacity/resource mgmt, failure modes, incident debugging, toil reduction.

Explicit out-of-scope (the ~20% - list in README "where to go next"): deep CNI internals,
multi-cluster/federation, service-mesh deep dive, building production-grade operators, cost/FinOps,
advanced storage (Ceph/Rook), Windows nodes.

---

## 9. Build order, batching & per-lab "definition of done"

**Order:** `00-cluster-setup` (README + eks.md + ovh.md + tooling.md + shared manifests) ->
`coverage-matrix.md` -> labs `01...20` in numeric order (dependencies only point backward).

**Batches for review:** A=01-05, B=06-08, C=09-11, D=12-13, E=14-15, F=16-17, G=18-20. Pause for user
review after each batch (the user asked for reviewable cadence).

**Per-lab definition of done:**
1. `lab.md` follows §6.1 order; every Step has a concrete command/manifest; Verify has an observable
 signal; Break-it reproduces the lecture's failure mode; Cleanup deletes the namespace + any cloud LB/volume.
2. `manifests/` apply cleanly on a fresh namespace; images pinned; resources requested; labels set.
3. Both `### EKS` and `### OVH` blocks present wherever a cloud primitive is used.
4. `lecture.md` follows §6.2 order; all five lenses present; AI/ML lens is conceptual (no GPU dependency);
 Pitfalls + Further-reading (cited chapter) present.
5. Row added to `coverage-matrix.md`.
6. No GPU resource requests anywhere; cost-bearing resources flagged.

**Writing style:** senior, concrete, failure-oriented. Lecture prose teaches the *why* and the
*production reality*, not kubectl help restated. Cite book chapters (on disk under
`/home/talium/LEARN/kubernetes_book/`) for depth.

---

## 10. Quick reference - what's already true in the repo

- Books extracted to markdown at `/home/talium/LEARN/kubernetes_book/{Kubernetes in Action, Kubernetes Patterns, Designing Distributed Systems}/`.
- A Python venv with PyMuPDF exists at `/home/talium/LEARN/.pdfvenv`; the extractor is `/home/talium/LEARN/build_books.py` (only needed for re-extraction).
- This spec lives at `/home/talium/LEARN/kubernetes_book/COURSE/PLAN.md`; the approved copy is also at `/home/talium/.claude/plans/study-these-books-and-zany-nygaard.md`.
- Nothing under `COURSE/` exists yet except this file - start by creating `README.md` and `00-cluster-setup/`.
