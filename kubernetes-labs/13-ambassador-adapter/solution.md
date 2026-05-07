# Lab 13 - Ambassador & Adapter · **Solution**
**Patterns:** Ambassador + Adapter **Source: KP "Ambassador"/"Adapter"; DDS ch4/5 Est:** 50 min

> The worked lab, with every command. Try the [exercise](lab.md) first; the checkpoint answers and the
> *why* are in [`lecture.md`](lecture.md).

## Objective
Decouple an app from network topology with an **Ambassador sidecar (the app talks only to `localhost`; the ambassador handles where the real backend lives), then normalize the app's operational interface with an Adapter sidecar (a redis that only speaks its native `INFO` gets a standard Prometheus `/metrics` endpoint). Reroute the backend with zero app change**, and produce uniform telemetry without touching the app.

## Concepts exercised
- **Ambassador** = an outbound proxy sidecar. App connects to `localhost:<port>`; the ambassador owns discovery/routing/retries/TLS to the real backend.
- **Adapter** = a translation sidecar. It reads the app's native stats and exposes them in a STANDARD interface (Prometheus `/metrics`).
- Shared pod network namespace (`localhost` between containers); shared lifecycle.
- ConfigMap-driven proxy target; `kubectl apply` overwrite + pod restart as the reroute mechanism.
- Prometheus exposition format (`# HELP` / `# TYPE` / metric lines); optional ServiceMonitor scrape.

## Prerequisites
- Labs **01** (kubectl, pods, labels), **06** (Services & DNS), **12** (init & sidecars - if you skipped it, just know that containers in one pod share network + lifecycle).
- A reachable cluster (`kubectl get nodes` -> 2-3 `Ready`). See `../00-cluster-setup/`.
- Optional: `kube-prometheus-stack` from `../00-cluster-setup/` §2.8 for the last (optional) scrape step.

## Setup
```bash
kubectl create namespace lab-13-ambassador-adapter
kubens lab-13-ambassador-adapter          # or add -n lab-13-ambassador-adapter to every command

# Two real backends (A and B) + the ambassador's first config (-> backend-A):
kubectl apply -f manifests/backend-a.yaml
kubectl apply -f manifests/backend-b.yaml
kubectl apply -f manifests/ambassador-config-a.yaml
kubectl get pods,svc,configmap
```
**Predict (0):** Open `manifests/pod-ambassador.yaml`. The `app` container connects to which address? Does that string mention `backend-a`, `backend-b`, or any Service name at all?

---

## Steps

### 1. Start the app + ambassador; prove writes land in backend-A
```bash
kubectl apply -f manifests/pod-ambassador.yaml
kubectl wait --for=condition=Ready pod/app-with-ambassador --timeout=60s
kubectl logs app-with-ambassador -c app --tail=4         # "wrote app:msg:N via localhost:6379"
```
The app writes keys every 2s to `localhost:6379`. Now look where they actually landed:
```bash
kubectl exec deploy/backend-a -- redis-cli DBSIZE        # > 0 and climbing
kubectl exec deploy/backend-b -- redis-cli DBSIZE        # 0 - B has nothing yet
kubectl exec deploy/backend-a -- redis-cli GET app:writes
```
**Observe (1):** `backend-a` DBSIZE grows; `backend-b` stays `0`. The app said `localhost`; the ambassador delivered to A.

### 2. Confirm the app truly only knows `localhost`
```bash
kubectl get pod app-with-ambassador -o jsonpath='{.spec.containers[?(@.name=="app")].args}' | tr ',' '\n' | grep -i 'localhost\|backend'
kubectl exec app-with-ambassador -c app -- sh -c 'redis-cli -h 127.0.0.1 -p 6379 ping'   # PONG, via the ambassador
```
**Observe (2):** The only network address in the app container is `127.0.0.1`/`localhost`. There is no `backend-a` anywhere in the app spec.

### 3. Reroute to backend-B by changing ONLY the ambassador config
You will edit nothing in `pod-ambassador.yaml`. You swap the ConfigMap and restart the pod (haproxy reads its config at start).
```bash
kubectl apply -f manifests/ambassador-config-b.yaml      # same ConfigMap name; one line differs
kubectl delete pod app-with-ambassador                   # restart so the ambassador re-reads config
kubectl apply -f manifests/pod-ambassador.yaml           # the SAME unchanged app manifest
kubectl wait --for=condition=Ready pod/app-with-ambassador --timeout=60s
```
**Predict (3):** After the restart, which backend's DBSIZE will now climb - A or B? Did you change a single byte of the app container?

### 4. Prove the reroute happened with zero app change
```bash
sleep 8
kubectl exec deploy/backend-b -- redis-cli DBSIZE        # now climbing
kubectl exec deploy/backend-a -- redis-cli DBSIZE        # frozen at its old value
kubectl exec app-with-ambassador -c app -- redis-cli -h 127.0.0.1 -p 6379 GET app:writes
```
**Prove it (4):** New writes appear in `backend-b`; `backend-a` is no longer growing. The app log still says "via localhost:6379" - it never noticed the topology moved underneath it.

### 5. Adapter: give a native-only redis a standard `/metrics`
```bash
kubectl apply -f manifests/pod-adapter.yaml
kubectl wait --for=condition=Ready pod/redis-with-adapter --timeout=60s
```
First, see the app's NATIVE interface (only RESP `INFO` - not scrapeable by Prometheus):
```bash
kubectl exec redis-with-adapter -c redis -- redis-cli INFO server | head -8
```
Now the ADAPTER's STANDARD interface on port 9121:
```bash
kubectl exec redis-with-adapter -c exporter -- wget -qO- localhost:9121/metrics | grep -E '^# (HELP|TYPE)|^redis_up|^redis_connected_clients' | head -12
```
**Observe (5):** The same redis now exposes `# HELP` / `# TYPE` / `redis_*` metric lines - Prometheus text translated from `INFO`. The redis container was never instrumented.

### 6. (Optional) Let Prometheus scrape the normalized endpoint
Only if `kube-prometheus-stack` is installed (cluster-setup §2.8). Otherwise skip - step 5 already proved the interface.
```bash
kubectl apply -f manifests/servicemonitor.yaml     # fails if the Operator CRDs are absent - that's fine, skip
# In the Prometheus UI (port-forward the stack), query: redis_up{namespace="lab-13-ambassador-adapter"}
```
**Observe (6):** One generic scrape rule ("scrape the metrics port") works because the adapter made the output standard - no redis-specific Prometheus config needed.

---

## Verify
```bash
# Reroute proven with no app change:
kubectl exec deploy/backend-b -- redis-cli DBSIZE          # > 0 (B now receives)
kubectl exec deploy/backend-a -- redis-cli DBSIZE          # frozen (A stopped receiving)
# Adapter exposes Prometheus format:
kubectl exec redis-with-adapter -c exporter -- wget -qO- localhost:9121/metrics | grep -c '^# TYPE'   # > 0
```
yes Success = writes follow the ambassador's target (now B) while the app manifest is byte-for-byte unchanged, AND `/metrics` returns Prometheus text.

---

## Break it - the ambassador is now in the request path
The ambassador decoupled the app from topology, but it also inserted itself into every request. Kill it and watch a healthy backend become unreachable *from the app's point of view*.
```bash
# backend-b is perfectly healthy:
kubectl exec deploy/backend-b -- redis-cli PING            # PONG

# Now break the ambassador's config (point it at a backend that doesn't exist):
kubectl create configmap ambassador-config \
  --from-literal=haproxy.cfg=$'global\n  maxconn 256\ndefaults\n  mode tcp\n  timeout connect 2s\n  timeout client 30s\n  timeout server 30s\nfrontend f\n  bind 127.0.0.1:6379\n  default_backend b\nbackend b\n  server target backend-does-not-exist:6379 check' \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl delete pod app-with-ambassador
kubectl apply -f manifests/pod-ambassador.yaml
kubectl wait --for=condition=Ready pod/app-with-ambassador --timeout=60s
sleep 6
kubectl logs app-with-ambassador -c app --tail=6
```
**Predict (B1):** `backend-b` answers `PONG` on its own. Will the app's writes succeed now? Where does the failure surface - in the backend, or in the ambassador hop?

**Observe (B2):** The app's `redis-cli` to `localhost:6379` now fails/hangs even though backend-b is healthy. The break is entirely in the ambassador hop - proof that you traded topology-coupling for a dependency on the sidecar (a latency point and a SPOF you must monitor).

**Restore:**
```bash
kubectl apply -f manifests/ambassador-config-b.yaml
kubectl delete pod app-with-ambassador
kubectl apply -f manifests/pod-ambassador.yaml
kubectl wait --for=condition=Ready pod/app-with-ambassador --timeout=60s
sleep 6
kubectl exec deploy/backend-b -- redis-cli DBSIZE          # climbing again
```

### EKS
No cloud-specific steps - everything here is pod-internal (shared netns, ConfigMap, sidecar). No LoadBalancer, no EBS. The optional ServiceMonitor needs the Prometheus Operator (kube-prometheus-stack) just as on any cluster.

### OVH
No cloud-specific steps - identical to EKS. Pod-internal only.

---

## Cleanup
```bash
kubectl delete namespace lab-13-ambassador-adapter
```
No cloud LB/volume was created in this lab - deleting the namespace is enough. (If you applied the ServiceMonitor, it lives in the namespace and is deleted with it.)

---
*Done? Now read [`lecture.md`](lecture.md) and grade your Predict predictions.*
