# Kubernetes + SRE Mastery

A 20-lab, hands-on course for getting to real working competence with Kubernetes and the SRE practice
around it: workloads and rollouts, health and lifecycle, config and storage, networking and
NetworkPolicy, scheduling, batch, autoscaling, RBAC and pod security, and an observability +
operators capstone. Every lab runs on a real managed cluster, CPU-only, with the cloud-specific bits
given for both EKS and OVH.

[![CI](https://github.com/Open-The-Gates/kubernetes-sre-mastery/actions/workflows/ci.yml/badge.svg)](https://github.com/Open-The-Gates/kubernetes-sre-mastery/actions/workflows/ci.yml)

> I built this to stop half-knowing Kubernetes. Each lab is something you actually apply to a cluster
> and watch happen, then a write-up that explains the mechanism and the failure modes. Distilled from
> Kubernetes in Action, Kubernetes Patterns, and Designing Distributed Systems.

## Why this exists

Most Kubernetes tutorials hand you the YAML and a green checkmark. You end up able to copy manifests
but unable to debug an empty Endpoints list or a stalled rollout. These labs do the opposite: you work
out the commands yourself, predict what will happen, then break things on purpose so you have already
seen the failure before it happens in production.

It is also deliberately CPU-only. The objective is Kubernetes mastery, not GPUs - so no lab requests
`nvidia.com/gpu` and nothing needs a GPU node pool. The GPU/ML angle lives only in each lecture's
AI/ML lens, as conceptual mapping (how the pattern applies to inference serving).

## How the labs work

Every lab is three files:

- `lab.md` - what to achieve and what to prove. The commands are deliberately left out; working out
  the `kubectl` invocation is the point. You hit checkpoints as you go:
  - Predict - guess the outcome before you run it
  - Observe - run a command and look at a specific field
  - Prove it - a concrete check that a claim holds
  - Break it - induce the failure mode on purpose
- `solution.md` - the worked commands, the output you should have seen, and the checkpoint answers.
- `lecture.md` - the write-up: the mechanism under the hood, then the same topic from five angles
  (Dev, DevOps/Platform, Architect, SRE, and AI/ML inference-serving mapping).

Suggested loop: read the objective, do the steps answering each checkpoint yourself, run Verify, run
Break-it, clean up, then read the lecture and see how you did.

## The labs

Phase A - foundations and core workloads (01-05), Phase B - networking (06-08), Phase C - storage and
stateful (09-11), Phase D - composition patterns (12-13), Phase E - scheduling (14-15), Phase F -
batch and async (16-17), Phase G - scaling, security, reliability (18-20).

| #  | Lab |
|----|-----|
| 00 | [Cluster setup](kubernetes-labs/00-cluster-setup/eks.md) (EKS / OVH / tooling) |
| 01 | [Cluster fundamentals & kubectl](kubernetes-labs/01-cluster-fundamentals/lab.md) |
| 02 | [Deployments, rollouts & rollback](kubernetes-labs/02-deployments/lab.md) |
| 03 | [Health probes & lifecycle](kubernetes-labs/03-health-lifecycle/lab.md) |
| 04 | [Config & secrets](kubernetes-labs/04-config-secrets/lab.md) |
| 05 | [Resources, QoS & quotas](kubernetes-labs/05-resources-qos/lab.md) |
| 06 | [Services, Endpoints & DNS](kubernetes-labs/06-services-dns/lab.md) |
| 07 | [Ingress, TLS & load-balanced serving](kubernetes-labs/07-ingress-tls/lab.md) |
| 08 | [NetworkPolicy & segmentation](kubernetes-labs/08-networkpolicy/lab.md) |
| 09 | [Volumes, PV/PVC & provisioning](kubernetes-labs/09-volumes-pv-pvc/lab.md) |
| 10 | [StatefulSets](kubernetes-labs/10-statefulsets/lab.md) |
| 11 | [Singleton, PDB & leader election](kubernetes-labs/11-singleton-pdb-leader/lab.md) |
| 12 | [Init containers & Sidecar](kubernetes-labs/12-init-sidecar/lab.md) |
| 13 | [Ambassador & Adapter](kubernetes-labs/13-ambassador-adapter/lab.md) |
| 14 | [Scheduling & placement](kubernetes-labs/14-scheduling/lab.md) |
| 15 | [DaemonSet & node agents](kubernetes-labs/15-daemonset/lab.md) |
| 16 | [Jobs, CronJobs & work queue](kubernetes-labs/16-jobs-cronjobs/lab.md) |
| 17 | [Event-driven & coordinated batch](kubernetes-labs/17-event-driven-batch/lab.md) |
| 18 | [Autoscaling: HPA, VPA, Cluster Autoscaler](kubernetes-labs/18-autoscaling/lab.md) |
| 19 | [RBAC, Pod Security & secrets](kubernetes-labs/19-rbac-podsecurity/lab.md) |
| 20 | [Observability, SLOs & operators (capstone)](kubernetes-labs/20-observability-operators/lab.md) |

See [`kubernetes-labs/coverage-matrix.md`](kubernetes-labs/coverage-matrix.md) for what each lab covers,
and [`kubernetes-labs/PLAN.md`](kubernetes-labs/PLAN.md) for the design spec it was built from.

## Getting started

You need a managed cluster (2-3 nodes) and the usual local tooling. Follow the setup notes for your
cloud, then start at lab 01 and go in order - later labs assume earlier ones.

```bash
git clone https://github.com/Open-The-Gates/kubernetes-sre-mastery.git
cd kubernetes-sre-mastery
$EDITOR kubernetes-labs/00-cluster-setup/eks.md   # or ovh.md
```

Tear the cluster down when you stop for the day - each lab's Cleanup removes its own namespace and any
cloud LoadBalancer/volume it created, but the cluster itself costs money while it runs.

### Browse it as a site (optional)

```bash
make serve        # docsify on localhost:3007
```

## Repo layout

```
.
├── README.md                  # this file (also the docsify home page)
├── _sidebar.md, index.html    # docsify
├── Makefile                   # serve / validate / lint
├── tools/check_manifests.py   # YAML parse check over every manifest
└── kubernetes-labs/
    ├── PLAN.md
    ├── coverage-matrix.md
    ├── 00-cluster-setup/
    └── NN-<slug>/{lab,solution,lecture}.md + manifests/
```

## Status / TODO

- [ ] Labs were exercised against EKS; the OVH paths are written and spot-checked but not run
      end-to-end on every lab. Corrections welcome.
- [ ] Lab 20's operator section uses a minimal reconcile loop; a kubebuilder version would be a nice
      follow-up.
- [ ] Could add a kind/minikube quickstart for the labs that do not need a cloud LoadBalancer.

## License

MIT - see [LICENSE](LICENSE).
