# Coverage matrix - proving the ~80%

One row per lab. Columns: K8s objects/mechanisms · KIA ch · KP pattern · DDS pattern · SRE pillar. Below the table, a checklist asserts every item in each family is touched by ≥1 lab. Rows marked yes are **built**; [ ] are **planned** (spec in `PLAN.md §7`).

| Built | # | Lab | K8s objects / mechanisms | KIA | KP pattern | DDS pattern | SRE pillar |
|---|----|-----|--------------------------|-----|------------|-------------|------------|
| yes | 01 | Cluster fundamentals | Pod, namespace, labels/selectors, annotations, ephemeral debug container, kubeconfig/context | 2,3 | - | - | incident debugging (events-first) |
| yes | 02 | Deployments & rollback | Deployment, ReplicaSet (pod-template-hash), RollingUpdate, rollout undo/history, minReadySeconds | 9 | Declarative Deployment | - | change management |
| yes | 03 | Health & lifecycle | liveness/readiness/startup probes, preStop, terminationGracePeriod, SIGTERM | 4,5,17 | Health Probe, Managed Lifecycle | health checks | failure modes, change mgmt |
| yes | 04 | Config & secrets | ConfigMap (env/file), Secret, immutable, Kustomize overlays | 7 | EnvVar/Config Resource/Immutable/Template | - | change management |
| yes | 05 | Resources, QoS & quotas | requests/limits, QoS classes, OOMKill, throttling, LimitRange, ResourceQuota | 14 | Predictable Demands | - | capacity/resource mgmt |
| yes | 06 | Services, Endpoints & DNS | Service (ClusterIP/NodePort/LB/headless), EndpointSlice, CoreDNS, kube-proxy | 5 | Service Discovery | Replicated svc | failure modes (empty endpoints) |
| yes | 07 | Ingress, TLS & LB serving | Ingress, IngressClass, cert-manager, multi-tier cache | 5 | - | Replicated Load-Balanced Service | capacity, tail latency |
| yes | 08 | NetworkPolicy & segmentation | NetworkPolicy (ingress/egress, default-deny), CNI | 13 | Network Segmentation | - | failure modes (DNS egress), blast radius |
| yes | 09 | Volumes, PV/PVC | emptyDir, PVC, StorageClass, reclaim policy, access modes, CSI | 6 | - | - | capacity, backup/restore |
| yes | 10 | StatefulSets | StatefulSet, volumeClaimTemplates, headless svc, ordinal identity, partitioned update | 10 | Stateful Service | - | failure modes (quorum/failover) |
| yes | 11 | Singleton, PDB & leader election | PDB, coordination.k8s.io/Lease, node drain/cordon | 4 | Singleton Service | Ownership Election | change mgmt (safe maintenance) |
| yes | 12 | Init & Sidecar | initContainers, native sidecar (restartPolicy:Always), shared emptyDir | 17 | Init Container, Sidecar | Sidecar | failure modes (sidecar tax) |
| yes | 13 | Ambassador & Adapter | proxy sidecar, metrics adapter, ServiceMonitor intro | - | Ambassador, Adapter | Ambassador, Adapter | observability (uniform /metrics) |
| yes | 14 | Scheduling & placement | nodeAffinity, pod(anti)affinity, topologySpreadConstraints, taints/tolerations | 16 | Automated Placement | - | failure modes (AZ loss), capacity |
| yes | 15 | DaemonSet & node agents | DaemonSet, nodeSelector, tolerations, rolling DS update | 4 | Daemon Service | Adapter (agents) | observability coverage |
| yes | 16 | Jobs, CronJobs & work queue | Job (parallelism/completions/Indexed), CronJob (concurrencyPolicy, TTL), work queue | 4 | Batch Job, Periodic Job | Work Queue | failure modes (obsolete work) |
| yes | 17 | Event-driven & coordinated batch | scatter/gather, event-driven chain, map/reduce, KEDA (optional) | - | - | Scatter/Gather, Event-Driven, Coordinated Batch | failure modes (tail latency, backpressure) |
| yes | 18 | Autoscaling | HPA (+behavior), VPA (concept), Cluster Autoscaler/Karpenter, PDB, scale-to-zero | 15 | Elastic Scale | - | capacity, scale-up vs SLO |
| yes | 19 | RBAC, Pod Security & secrets | SA, Role/RoleBinding, securityContext, Pod Security Standards, External/Sealed Secrets, IRSA | 12,13 | Access Control, Process Containment, Secure Configuration | - | failure modes (privilege), audit |
| yes | 20 | Observability, SLOs, failure patterns, operators (capstone) | Prometheus/ServiceMonitor, PrometheusRule, OTel traces, CRD + controller | 18 | Controller, Operator | Monitoring, Failure Patterns | **all SRE pillars** - SLO/error budget, alerting, toil |

---

## Coverage checklist (every item must be hit by ≥1 lab)

**Workloads:** Pod (01) · ReplicaSet (02) · Deployment (02) · StatefulSet (10) · DaemonSet (15) · Job (16) · CronJob (16). yes complete in plan.

**Networking:** Service ClusterIP/NodePort/LB/headless (06) · Endpoints/EndpointSlice (03,06) · DNS (06) · Ingress (07) · NetworkPolicy (08).
**Config/Storage:** ConfigMap (04) · Secret (04,19) · PV/PVC (09,10) · StorageClass (09) · CSI (09, setup) · volume types/emptyDir (09,12).
**Scheduling:** requests/limits/QoS (05) · LimitRange (05) · ResourceQuota (05) · affinity/anti-affinity (14) · topology spread (14) · taints/tolerations (14,15) · PriorityClass (05 lecture, 14).
**Scaling/Resilience:** HPA (18) · VPA (18, concept) · Cluster Autoscaler/Karpenter (18, setup) · PDB (11,18) · leader election (11).
**Security:** RBAC (19) · ServiceAccount (19) · securityContext (19) · Pod Security Standards (19) · secret management (04,19) · IRSA / OVH identity gap (19, setup).
**Patterns - KP (all 6 parts):** Foundational (02,03,05,14) · Behavioral (06,10,11,15,16) · Structural (12,13) · Configuration (04) · Security (08,19) · Advanced (18,20).
**Patterns - DDS:** single-node sidecar/ambassador/adapter (12,13) · serving replicated/sharded/scatter-gather/election (07,17,11) · batch work-queue/event-driven/coordinated (16,17) · observability + failure patterns (20).
**Extending K8s:** CRD (20) · controller (20) · operator (20).
**SRE pillars:** observability logs/metrics/traces (13,20) · SLI/SLO/error budgets (20) · change management (02,03,04) · capacity/resource mgmt (05,09,18) · failure modes & resilience (03,08,10,11,16,17,20) · incident debugging (01,06) · toil reduction via operators (20).
---

## Explicit out-of-scope (the ~20% - see README "where to go next")
Deep CNI internals · multi-cluster / federation · service-mesh deep dive (Istio/Linkerd internals) · building production-grade operators · cost/FinOps · advanced storage (Ceph/Rook) · Windows nodes.

> As labs are built, flip [ ] -> yes in the table. When all 20 are yes and every checklist line resolves, the ~80% claim is demonstrably met.
