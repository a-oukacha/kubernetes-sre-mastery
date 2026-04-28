# Lab 07 - Ingress, TLS & replicated load-balanced serving · **Solution**
**Patterns:** Replicated Load-Balanced Service **Source: KIA 5; DDS "Replicated Load-Balanced Services" Est:** 55 min

> The worked lab, with every command. Try the [exercise](lab.md) first; the checkpoint answers and the
> *why* are in [`lecture.md`](lecture.md).

## Objective
Put one cloud load balancer in front of the whole cluster and do real **L7 work** at the edge: route by **host + path to two different replicated backends, terminate TLS with a cert-manager-issued certificate, then slot a caching tier** between the edge and an app and **prove the cache offloads the backend. Finish by removing the cache under the same load to see why, past a certain request rate, caching is not optional**.

## Concepts exercised
- Ingress object vs the ingress-nginx **controller** (config vs the thing that acts on it)
- `IngressClass` / `ingressClassName` - how a controller decides an Ingress is "its"
- Host + path routing rules; `rewrite-target`
- TLS termination at the edge; cert-manager `Issuer` + `Certificate` -> a `kubernetes.io/tls` Secret
- Multi-tier topology: **edge -> cache -> app**; L7 (controller) vs L4 (ClusterIP Service)
- `proxy_cache` HIT/MISS, cache-key, request collapsing (`proxy_cache_lock`)
- Session-affinity pitfalls across tiers

## Prerequisites
- Labs 01-06 done (labels/selectors, Deployments & rollouts, probes, config, requests, Services & endpoints).
- A reachable cluster (`kubectl get nodes` -> 2-3 `Ready`). See `../00-cluster-setup/`.
- **Add-ons installed:** **ingress-nginx** and **cert-manager** - per `../00-cluster-setup/eks.md §2.5/§2.6` (OVH: `ovh.md §2.4`). Confirm:
  ```bash
  kubectl -n ingress-nginx get deploy ingress-nginx-controller   # 1/1 available
  kubectl get ingressclass                                       # an entry named "nginx"
  kubectl -n cert-manager get deploy                             # cert-manager, -cainjector, -webhook all available
  ```

## Setup
```bash
kubectl create namespace lab-07-ingress-tls
kubens lab-07-ingress-tls          # or add -n lab-07-ingress-tls to every command
```
**Cost guard:** this lab creates **no new cloud LB. The single billed `LoadBalancer` is the shared `ingress-nginx-controller` Service** in the `ingress-nginx` namespace (installed in cluster-setup, torn down there). Everything *this* lab creates - Ingresses, the Certificate/Secret, Services, Deployments - is namespaced and removed by deleting `lab-07-ingress-tls`.

**Predict (0): You're about to create two Ingress objects but no** `type=LoadBalancer` Service. How many cloud load balancers will exist for *all* the routing in this lab - and where does north-south traffic physically enter the cluster?

---

## Steps

### 1. Find the one front door - the controller's external address
```bash
kubectl -n ingress-nginx get svc ingress-nginx-controller -o wide
# grab the external address into a shell var:
export LB=$(kubectl -n ingress-nginx get svc ingress-nginx-controller \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}{.status.loadBalancer.ingress[0].ip}')
echo "LB=$LB"
```
**Observe (1): `EXTERNAL-IP` is the address of the cloud LB that fronts the controller**, not any app. On EKS it's an **NLB hostname**; on OVH it's an **IP**. Note the `ports` column: `80` and `443` both map to the controller. This one Service is the only `type=LoadBalancer` in play.

### 2. Deploy the two replicated backends
```bash
kubectl apply -f manifests/backend-a.yaml      # podinfo 6.7.0, blue, 2 replicas
kubectl apply -f manifests/backend-b.yaml      # podinfo 6.7.1, green, 2 replicas
kubectl rollout status deploy/backend-a
kubectl rollout status deploy/backend-b
kubectl get deploy,svc -l app.kubernetes.io/part-of=k8s-sre-course
```
**Observe (2): Each backend is a Deployment behind a ClusterIP** Service - no external address of its own. They are reachable only *inside* the cluster (and, in a moment, through the Ingress). Confirm `backend-a` and `backend-b` have **no** `EXTERNAL-IP`.

### 3. Apply the path-routing Ingress (HTTP only, no TLS yet)
```bash
kubectl apply -f manifests/ingress-routing.yaml
kubectl get ingress demo-routing
kubectl describe ingress demo-routing | sed -n '/Rules:/,/Annotations:/p'
```
**Predict (3): The Ingress sets `ingressClassName: nginx` and routes `/a`->`backend-a`, `/b`->`backend-b` on host `lab07.demo.local`. Before you curl: will a request to `http://$LB/a` with no** `Host: lab07.demo.local` header match this rule? Why might the controller answer `404` instead of routing to A?

### 4. Curl the two paths - prove each lands on the right backend
We don't own DNS for `lab07.demo.local`, so we tell curl to resolve that name to the LB with `--resolve`:
```bash
curl -s --resolve lab07.demo.local:80:$LB http://lab07.demo.local/a/version ; echo
curl -s --resolve lab07.demo.local:80:$LB http://lab07.demo.local/b/version ; echo
# the UI message is even clearer:
curl -s --resolve lab07.demo.local:80:$LB http://lab07.demo.local/a/ | grep -o 'backend [AB][^<"]*'
curl -s --resolve lab07.demo.local:80:$LB http://lab07.demo.local/b/ | grep -o 'backend [AB][^<"]*'
```
**Prove it (4):** `/a/version` reports podinfo **6.7.0 ("backend A - blue"); `/b/version` reports 6.7.1 ("backend B - green"). Same host, same LB, same port - only the path decided the backend. That decision was made at L7**, inside the controller.

### 5. Issue a TLS cert with cert-manager
```bash
kubectl apply -f manifests/tls-issuer-cert.yaml
kubectl get certificate lab07-tls -w        # wait for READY=True, then Ctrl-C
kubectl get secret lab07-tls                 # type kubernetes.io/tls (tls.crt + tls.key)
```
**Observe (5):** You created an `Issuer` and a `Certificate` - you never ran `openssl`. What object did cert-manager *produce*, and what is its `TYPE`? (`kubectl get secret lab07-tls -o jsonpath='{.type}'; echo`.)

### 6. Switch the Ingress to terminate TLS
```bash
kubectl apply -f manifests/ingress-tls.yaml     # same routing + a tls: block referencing lab07-tls
kubectl get ingress demo-routing                # PORTS now shows 80, 443
```
**Prove it (6):** HTTPS now works at the edge (self-signed, so `-k` skips trust validation), and plain HTTP redirects to it:
```bash
curl -sk --resolve lab07.demo.local:443:$LB https://lab07.demo.local/a/version ; echo   # 6.7.0 over TLS
# inspect the served cert:
curl -skv --resolve lab07.demo.local:443:$LB https://lab07.demo.local/a/ 2>&1 \
  | grep -E 'subject:|issuer:|SSL connection'
# http now 308-redirects to https:
curl -s -o /dev/null -w '%{http_code} -> %{redirect_url}\n' \
  --resolve lab07.demo.local:80:$LB http://lab07.demo.local/a/version
```
Confirm the served certificate's `subject`/`issuer` is the self-signed cert-manager cert for `lab07.demo.local`, and HTTP returns `308` to the `https://` URL.

### 7. Insert the caching tier in front of backend A
```bash
kubectl apply -f manifests/cache.yaml           # nginx proxy_cache -> backend-a
kubectl rollout status deploy/cache
# first request is a MISS, second is a HIT - watch the header:
kubectl run cinspect --rm -it --restart=Never --image=curlimages/curl:8.10.1 -- \
  sh -c 'curl -si http://cache/delay/0.1 | grep -i x-cache; echo ---; curl -si http://cache/delay/0.1 | grep -i x-cache'
```
**Observe (7): The first response carries `X-Cache-Status: MISS`, the second `X-Cache-Status: HIT`. The HIT was served by nginx**, not podinfo - note that the 100ms `/delay` was paid only once.

### 8. Drive load THROUGH the cache and count backend hits
```bash
# zero the meter: note backend-a's current request count, then run load.
kubectl logs -l app.kubernetes.io/name=backend-a --tail=1 >/dev/null 2>&1   # warm log handles
kubectl apply -f manifests/loadgen-cache.yaml
kubectl wait --for=condition=Ready pod/loadgen-cache --timeout=30s
kubectl logs -f loadgen-cache                   # ~1600 requests sent; watch the summary
```
**Predict (8): `loadgen-cache` sends ~1600 requests at the same `/delay/0.1` URL. How many of those reach a backend-a pod**? Predict the order of magnitude before you count it in the next step.

---

## Verify
```bash
# (a) right backend per path, over TLS:
curl -sk --resolve lab07.demo.local:443:$LB https://lab07.demo.local/a/version | grep -o '"version":"[^"]*"'   # 6.7.0
curl -sk --resolve lab07.demo.local:443:$LB https://lab07.demo.local/b/version | grep -o '"version":"[^"]*"'   # 6.7.1

# (b) TLS cert is served and Ready:
kubectl get certificate lab07-tls -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}'; echo   # True

# (c) the cache offloaded the backend - count requests fortio sent vs requests backend-a logged:
kubectl logs loadgen-cache | grep -E 'All done|Code 200'                      # ~1600 sent, ~all 200
kubectl logs -l app.kubernetes.io/name=backend-a --prefix --tail=-1 | grep -c 'HTTP/1.1" 200'   # FAR fewer (single digits)
```
yes Success = `/a`->6.7.0 and `/b`->6.7.1 over `https://`; the Certificate is `Ready=True`; and backend-a logged only a handful of requests while fortio sent ~1600 - the rest were `X-Cache-Status: HIT` served by nginx.

---

## Break it - caching is not optional under load
Now aim the **same** load straight at the backend, with no cache between them.
```bash
kubectl apply -f manifests/loadgen-direct.yaml      # same QPS/duration/URL, target = backend-a directly
kubectl wait --for=condition=Ready pod/loadgen-direct --timeout=30s
kubectl logs -f loadgen-direct                      # let it finish (~20s)
```
**Predict (B1):** `backend-a` is **2 small replicas, each request sleeps 100ms (`/delay/0.1`), and fortio pushes 80 req/s. Roughly how many in-flight requests must a replica juggle to keep up? Predict what happens to p99 latency** and the **error/timeout count** compared to the cached run.

**Observe (B2):** Compare the two fortio runs side by side:
```bash
kubectl logs loadgen-cache  | sed -n '/Sockets used/,/Code /p'   # cached: low p99, ~0 timeouts
kubectl logs loadgen-direct | sed -n '/Sockets used/,/Code /p'   # direct: p99 spikes, non-200 / -1 timeouts appear
kubectl logs loadgen-direct | grep -E 'percentile 99|Code (200|-1)|target 99'
```
What happened to the `99%` latency percentile and the count of `Code -1` (timeout) / non-200 responses once the cache was gone? The backend didn't get *slower per request* - it ran out of **concurrency**.

**Observe (B3):** While `loadgen-direct` is running, watch the backend pods feel it:
```bash
kubectl top pods -l app.kubernetes.io/name=backend-a    # CPU climbs toward its limit
kubectl get pods -l app.kubernetes.io/name=backend-a    # still Running - saturated, not crashed
```
Were the backend pods unhealthy, or simply **saturated**? (This is illustrative load, not a stress test - it finishes in ~20s and harms nothing.)

---

## Cleanup
```bash
kubectl delete namespace lab-07-ingress-tls
```
This deletes the Ingresses, the `Issuer`/`Certificate`/TLS Secret, both backends, the cache, and the loadgen pods. The shared `ingress-nginx-controller` LoadBalancer lives in the `ingress-nginx` namespace and is *not* touched here - it is uninstalled in cluster-setup teardown (`helm uninstall ingress-nginx -n ingress-nginx`), which releases the cloud LB. cert-manager (cluster-scoped CRDs + controllers) likewise stays installed for other labs.

### EKS
The controller's `EXTERNAL-IP` is an **NLB hostname (`...elb.amazonaws.com`); `--resolve` against that hostname works. Alternative: the AWS Load Balancer Controller can satisfy an `Ingress` with `ingressClassName: alb` by provisioning an ALB** directly (L7 at the cloud edge, no in-cluster nginx). We use ingress-nginx so the manifests are identical on OVH - see the lecture's DevOps note.

### OVH
The controller's `EXTERNAL-IP` is an **IP** from an OVH Load Balancer; `--resolve lab07.demo.local:443:<IP>` works the same way. There is no ALB-equivalent managed L7 ingress - ingress-nginx (or another in-cluster controller) is the path. The billed resource is the OVH LB fronting the controller.

---
*Now read [`lecture.md`](lecture.md) and grade your Predict predictions.*
