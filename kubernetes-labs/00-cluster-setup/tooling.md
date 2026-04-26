# Local tooling & demo images

Install once; used by every lab.

---

## CLIs

| Tool | Why | Install (macOS/Linux) |
|------|-----|------------------------|
| `kubectl` | the core CLI | `brew install kubectl` / [official docs] |
| `helm` | install charts (ingress, prometheus, ...) | `brew install helm` |
| `eksctl` | provision EKS | `brew install eksctl` |
| `awscli` v2 | EKS auth | `brew install awscli` |
| `kubens` / `kubectx` | switch namespace/context fast | `brew install kubectx` |
| `stern` | tail logs across many pods | `brew install stern` |
| `k9s` | terminal UI for the cluster | `brew install k9s` |
| `fortio` | HTTP load generation (labs 02,03,07,18) | `brew install fortio` or run `fortio/fortio` in-cluster |
| `jq` / `yq` | JSON/YAML wrangling | `brew install jq yq` |

> Set a default editor for `kubectl edit`: `export KUBE_EDITOR="nano"` (or vim).

**Quality-of-life:** alias `k=kubectl`, enable completion (`source <(kubectl completion bash)`), and use `kubens lab-NN-<slug>` at the start of each lab so you don't repeat `-n`.

---

## Demo images (pinned, CPU-only, no build required)

The course standardizes on off-the-shelf images so you never build anything:

| Image (pinned) | Used for |
|----------------|----------|
| `registry.k8s.io/e2e-test-images/agnhost:2.47` | Swiss-army test server: `netexec` HTTP echo, liveness/readiness toggling, graceful shutdown, `serve-hostname`. **Primary demo app.** |
| `ghcr.io/stefanprodan/podinfo:6.7.0` | production-like app with `/metrics`, health endpoints, versioned UI - deployment/canary/observability labs |
| `kennethreitz/httpbin:latest` *(pin a digest if you can)* | rich HTTP behaviors (status codes, delay) for probes/ingress |
| `polinux/stress:1.0.4` | CPU/memory pressure for OOM/throttle/QoS (lab 05) |
| `redis:7.2-alpine` | stateful / sharded-cache labs (10, 13) |
| `busybox:1.36` | init containers, sidecars, debug shells, `wget` loops |
| `alpine:3.20` | tiny debug/util container |
| `fortio/fortio:1.60.3` | in-cluster HTTP load generator |
| `bitnami/kubectl:1.30` | in-cluster kubectl for leader-election / work-queue manager / controller labs (11, 16, 20) |

> Pin tags (never `:latest`) in real manifests. Where a `:latest`-only image is listed, resolve and pin its digest in your own copy.

### agnhost cheat-sheet (you'll use this constantly)
```bash
# HTTP echo server on :8080 that prints request info:
args: ["netexec", "--http-port=8080"]
# Endpoints agnhost netexec actually exposes:
#   /              -> "NOW: <timestamp>"
#   /hostname      -> pod hostname
#   /healthz       -> 200 (or 412 once /exit-ish state set) - NOT an externally flippable toggle
#   /shutdown      -> begins graceful shutdown (useful for lifecycle demos)
#   /exit          -> process exits with a chosen code
#   /shell?cmd=... -> run a command (handy in labs)
```
> **Important (corrected):** `netexec` does **not** expose freely HTTP-togglable `/healthz` *and* `/readyz`
> endpoints you can flip on demand. When a lab needs to deterministically flip **readiness** or
> **liveness at will (lab 03), wire the probes as exec checks against sentinel files**
> (e.g. `readinessProbe: exec [ "test", "-f", "/tmp/ready" ]`) and flip them with
> `kubectl exec <pod> -- touch/rm /tmp/ready`. agnhost still serves real HTTP traffic for the Service
> backend; the sentinel files just give you a reliable switch. Lab 03's manifests show the exact wiring.

---

## Helm charts used across the course (pin versions)

| Chart | Labs | Repo |
|-------|------|------|
| `ingress-nginx` | 07 | `https://kubernetes.github.io/ingress-nginx` |
| `cert-manager` | 07 | `https://charts.jetstack.io` |
| `kube-prometheus-stack` | 13, 20 | `https://prometheus-community.github.io/helm-charts` |
| `metrics-server` | 05, 18 (if not an add-on) | `https://kubernetes-sigs.github.io/metrics-server/` |
| `keda` *(optional)* | 17, 18 | `https://kedacore.github.io/charts` |
| `external-secrets` / `sealed-secrets` *(optional)* | 19 | ESO / Bitnami repos |

---

## Sanity check before lab 01
```bash
kubectl get nodes                 # 2-3 Ready, multiple zones
kubectl top nodes                 # metrics-server works
kubectl get sc                    # a default StorageClass exists
kubectl auth can-i create namespaces   # yes
```
If all four pass, go to [`../01-cluster-fundamentals/lab.md`](../01-cluster-fundamentals/lab.md).
