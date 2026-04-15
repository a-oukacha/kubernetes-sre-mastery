# Kubernetes + SRE Mastery - A 20-Lab Cookbook

A hands-on course that takes you to ~80% practical mastery of Kubernetes + its design patterns + SRE practice. Twenty labs you *run* on a real managed cluster, each paired with a *lecture* that explains what you just did from five senior lenses.

Built from three books (extracted to markdown under `../`):

| Code | Book | Role |
|------|------|------|
| **KIA** | *Kubernetes in Action* - Luksa | Core mechanics (the "how") |
| **KP** | *Kubernetes Patterns* - Ibryam & Huß | 30 reusable patterns (the "shape/why") |
| **DDS** | *Designing Distributed Systems* - Burns | Serving/batch/observability patterns (the "system why") |

---

## How this course works (read this first)

Each lab is **two files**:

- **`lab.md`** - you *do* things in the cluster. It gives you the commands and manifests, tells you exactly **what to observe** and **what to prove** - but it **does not hand you the explanation**. You'll hit checkpoints like:
 - **Predict** - guess the outcome *before* you run the command.
 - **Observe** - run a command and look at a specific field.
 - **Prove it** - a concrete check that demonstrates a claim is true (or false).
 - **Break it** - deliberately induce the failure mode so you've *seen* it.

 The lab never tells you *why*. That's on purpose. You form a hypothesis; the lecture confirms or corrects it.

- **`lecture.md`** - the *course* built on what you just did. It opens with **"Answers to the lab checkpoints" (every Predict resolved), then explains the mechanism under the hood, then re-examines the topic from five lenses**:
 1. **Dev** - what an app developer must know/change in code.
 2. **DevOps / Platform** - how it's operated; the tooling; day-2 concerns.
 3. **Architect** - trade-offs; when to use vs avoid; alternatives.
 4. **SRE** - failure modes, SLOs, toil, what pages you at 3am, how to debug.
 5. **AI/ML - how the pattern maps to LLM/ML inference serving (conceptual; vLLM, KServe, GPU scheduling, KV-cache, TTFT/TPOT, DCGM). You never need a GPU to finish a lab.**

**Workflow per lab:** read the Objective -> do the Steps, answering every Predict yourself -> run Verify -> run Break-it -> run Cleanup -> *then* read the lecture and grade your predictions.

---

## Hard rules of this course

1. **Labs are CPU-only.** No lab requests `nvidia.com/gpu`; no GPU node pool is ever required. GPU/LLM content lives only in the lecture's *AI/ML* lens, as conceptual mapping. Cost stays ~zero.
2. **EKS primary, OVH MKS secondary. Every cloud-specific step (LoadBalancer, CSI/StorageClass, autoscaler, identity) is given for both** clouds in `### EKS` / `### OVH` blocks. The generic body runs on any conformant cluster.
3. **One namespace per lab** (`lab-NN-<slug>`), created in Setup and deleted in Cleanup. Never `default`.
4. Pinned image tags, requests on every pod, labels on everything. Labs are good cluster citizens, schedulable on a 2-3 node cluster.
5. **Cost guard.** Any LoadBalancer / EBS / EFS / NAT a lab creates is flagged and torn down in Cleanup.

---

## The 20 labs

### Phase A - Foundations & core workloads
| # | Lab | Patterns |
|---|-----|----------|
| 01 | Cluster fundamentals & kubectl fluency | - |
| 02 | Deployments, rolling updates & rollback | Declarative Deployment |
| 03 | Health probes & graceful lifecycle | Health Probe, Managed Lifecycle |
| 04 | Configuration & secrets | EnvVar / Config Resource / Immutable / Template |
| 05 | Resource requests/limits, QoS & quotas | Predictable Demands |

### Phase B - Networking & service discovery
| # | Lab | Patterns |
|---|-----|----------|
| 06 | Services, Endpoints & cluster DNS | Service Discovery |
| 07 | Ingress, TLS & load-balanced serving | Replicated Load-Balanced Service |
| 08 | NetworkPolicy & segmentation | Network Segmentation |

### Phase C - Storage & stateful
| # | Lab | Patterns |
|---|-----|----------|
| 09 | Volumes, PV/PVC & dynamic provisioning | - |
| 10 | StatefulSets | Stateful Service |
| 11 | Singleton, PDB & leader election | Singleton Service, Ownership Election |

### Phase D - Composition patterns
| # | Lab | Patterns |
|---|-----|----------|
| 12 | Init containers & Sidecar | Init Container, Sidecar |
| 13 | Ambassador & Adapter | Ambassador, Adapter |

### Phase E - Scheduling & node-level
| # | Lab | Patterns |
|---|-----|----------|
| 14 | Scheduling: affinity, topology spread, taints | Automated Placement |
| 15 | DaemonSet & node agents | Daemon Service |

### Phase F - Batch & async
| # | Lab | Patterns |
|---|-----|----------|
| 16 | Jobs, CronJobs & work queue | Batch Job, Periodic Job, Work Queue |
| 17 | Event-driven & coordinated batch | Scatter/Gather, Event-Driven, Coordinated Batch |

### Phase G - Scaling, security, reliability
| # | Lab | Patterns |
|---|-----|----------|
| 18 | Autoscaling: HPA, VPA, Cluster Autoscaler | Elastic Scale |
| 19 | RBAC, Pod Security & secrets | Access Control, Process Containment, Secure Configuration |
| 20 | Observability, SLOs, failure patterns & operators (capstone) | Monitoring, Failure Patterns, Controller/Operator |

---

## Getting started

1. **Provision a cluster and install add-ons: follow [`00-cluster-setup/eks.md`](00-cluster-setup/eks.md) or** [`00-cluster-setup/ovh.md`](00-cluster-setup/ovh.md).
2. **Install local tooling** (`kubectl`, `helm`, `kubens`, `stern`, `k9s`, `fortio`): [`00-cluster-setup/tooling.md`](00-cluster-setup/tooling.md).
3. **Verify** you can reach the cluster: `kubectl get nodes` shows 2-3 `Ready` nodes across multiple zones.
4. Start at [`01-cluster-fundamentals/lab.md`](01-cluster-fundamentals/lab.md). Do labs in order - later labs assume earlier knowledge.
5. Track coverage with [`coverage-matrix.md`](coverage-matrix.md).

> **Tear down when idle.** A managed cluster + LoadBalancers + volumes cost money. Each lab's Cleanup removes its own resources; delete the cluster entirely when you stop for the day (`eksctl delete cluster` / OVH Manager).

---

## Where to go next (the deliberate ~20% this course skips)

Deep CNI internals · multi-cluster / federation · service-mesh deep dive (Istio/Linkerd internals) · building production-grade operators · cost/FinOps · advanced storage (Ceph/Rook) · Windows nodes. Each lecture points you to the relevant book chapter for depth.
