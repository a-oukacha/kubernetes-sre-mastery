# Lab 07 - Ingress, TLS & replicated load-balanced serving · **Exercise**
**Patterns:** Replicated Load-Balanced Service **Source: KIA 5; DDS "Replicated Load-Balanced Services" Est:** 55 min

> **This is the exercise - the commands are deliberately *not* given.** Your job is to work out the
> `kubectl`/`curl` invocations yourself; that *is* the skill. Attempt every task and write down your answer
> to every **Predict / Observe / Prove it / Break it** before peeking. When you're stuck or done, [`solution.md`](solution.md) has
> the exact commands + the output you should have seen + every checkpoint answer. Then read
> [`lecture.md`](lecture.md) for the course.

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
- A reachable cluster (2-3 `Ready` nodes). See `../00-cluster-setup/`.
- **Add-ons installed:** **ingress-nginx** and **cert-manager** - per `../00-cluster-setup/eks.md §2.5/§2.6` (OVH: `ovh.md §2.4`). Before starting, confirm the `ingress-nginx-controller` Deployment is available, that an `IngressClass` named `nginx` exists, and that the three cert-manager Deployments (controller, cainjector, webhook) are all available.

## Setup
Create a namespace **`lab-07-ingress-tls`** and make it your default for the rest of this lab (so you don't have to pass `-n` every time).

**Cost guard:** this lab creates **no new cloud LB. The single billed `LoadBalancer` is the shared `ingress-nginx-controller` Service** in the `ingress-nginx` namespace (installed in cluster-setup, torn down there). Everything *this* lab creates - Ingresses, the Certificate/Secret, Services, Deployments - is namespaced and removed by deleting `lab-07-ingress-tls`.

**Predict (0): You're about to create two Ingress objects but no** `type=LoadBalancer` Service. How many cloud load balancers will exist for *all* the routing in this lab - and where does north-south traffic physically enter the cluster?

---

## Tasks

### 1. Find the one front door - the controller's external address
Inspect the `ingress-nginx-controller` Service in the `ingress-nginx` namespace and capture its external address into a shell variable (call it `LB`) - you'll reuse it in every curl below. Note which ports the Service exposes.

**Observe (1): The external address belongs to the cloud LB that fronts the controller**, not any app. On EKS it's an **NLB hostname**; on OVH it's an **IP**. Which ports map to the controller, and how many `type=LoadBalancer` Services are in play across the whole lab?

### 2. Deploy the two replicated backends
Apply `manifests/backend-a.yaml` (podinfo 6.7.0, blue, 2 replicas) and `manifests/backend-b.yaml` (podinfo 6.7.1, green, 2 replicas). Wait for both rollouts to finish, then list the Deployments and Services for the course's `part-of` label.

**Observe (2): Each backend is a Deployment behind a ClusterIP Service - no external address of its own. Confirm `backend-a` and `backend-b` have no** `EXTERNAL-IP`: where, then, can they be reached from?

### 3. Apply the path-routing Ingress (HTTP only, no TLS yet)
Apply `manifests/ingress-routing.yaml` and inspect the resulting `demo-routing` Ingress, paying attention to its routing rules.

**Predict (3): The Ingress sets `ingressClassName: nginx` and routes `/a`->`backend-a`, `/b`->`backend-b` on host `lab07.demo.local`. Before you curl: will a request to the LB hitting `/a` with no** `Host: lab07.demo.local` header match this rule? Why might the controller answer `404` instead of routing to A?

### 4. Curl the two paths - prove each lands on the right backend
You don't own DNS for `lab07.demo.local`, so make curl resolve that name to your `LB` address. Request the version endpoint under `/a` and under `/b`, and also fetch each path's UI page so you can read the backend identifier.

**Prove it (4): Show that `/a/version` reports podinfo 6.7.0 ("backend A - blue") and `/b/version` reports 6.7.1 ("backend B - green"). Same host, same LB, same port - convince yourself only the path decided the backend, and that the decision was made at L7**, inside the controller.

### 5. Issue a TLS cert with cert-manager
Apply `manifests/tls-issuer-cert.yaml`, then wait for the `lab07-tls` Certificate to become `Ready=True`. Look at what landed in the cluster as a result.

**Observe (5):** You created an `Issuer` and a `Certificate` - you never ran `openssl`. What object did cert-manager *produce* on your behalf, and what is its `TYPE`?

### 6. Switch the Ingress to terminate TLS
Apply `manifests/ingress-tls.yaml` (same routing plus a `tls:` block referencing the `lab07-tls` Secret) and re-inspect the `demo-routing` Ingress.

**Prove it (6):** Reach `/a` over **HTTPS (the cert is self-signed, so you'll skip trust validation), inspect the served certificate's subject and issuer, and probe what a plain-HTTP** request to `/a` now returns. Confirm the served cert is the self-signed cert-manager cert for `lab07.demo.local`, and that HTTP returns a `308` redirect to the `https://` URL.

### 7. Insert the caching tier in front of backend A
Apply `manifests/cache.yaml` (an nginx `proxy_cache` in front of `backend-a`) and wait for its rollout. From inside the cluster, hit the cache twice at the same delayed URL and watch the cache-status header on each response.

**Observe (7):** The first response should carry `X-Cache-Status: MISS`, the second `X-Cache-Status: HIT`. Who served the HIT - nginx or podinfo? Was the URL's 100ms delay paid once or twice?

### 8. Drive load THROUGH the cache and count backend hits
Apply `manifests/loadgen-cache.yaml`, wait for the loadgen pod to be Ready, and follow its output until it reports its summary (~1600 requests sent at the same delayed URL). Keep the backend-a request count in mind for the next step.

**Predict (8): `loadgen-cache` sends ~1600 requests at the same `/delay/0.1` URL. How many of those reach a backend-a pod**? Predict the order of magnitude before you count it.

---

## Verify
Demonstrate success with observable signals:
- **(a) Over `https://`, `/a` resolves to podinfo 6.7.0** and `/b` to **6.7.1**.
- **(b)** The `lab07-tls` Certificate's `Ready` condition reads `True`.
- **(c)** Count the requests fortio sent through the cache versus the requests `backend-a` actually logged, and show the backend saw far fewer.

yes Success = `/a`->6.7.0 and `/b`->6.7.1 over `https://`; the Certificate is `Ready=True`; and backend-a logged only a handful of requests while fortio sent ~1600 - the rest were cache HITs served by nginx.

---

## Break it - caching is not optional under load
Now aim the **same** load straight at the backend, with no cache between them: apply `manifests/loadgen-direct.yaml` (same QPS/duration/URL, but targeting `backend-a` directly), wait for it to be Ready, and let it finish (~20s).

**Predict (B1):** `backend-a` is **2 small replicas, each request sleeps 100ms (`/delay/0.1`), and fortio pushes 80 req/s. Roughly how many in-flight requests must a replica juggle to keep up? Predict what happens to p99 latency** and the **error/timeout count** compared to the cached run.

**Observe (B2):** Compare the two fortio runs side by side - the cached run versus the direct run - looking at the `99%` latency percentile and the count of `Code -1` (timeout) / non-200 responses. What changed once the cache was gone? (Note: the backend didn't get *slower per request* - think about what resource it ran out of.)

**Observe (B3): While `loadgen-direct` is running, watch the backend pods feel it - check their CPU against their limit and their pod status. Were the backend pods unhealthy, or simply saturated**? (This is illustrative load, not a stress test - it finishes in ~20s and harms nothing.)

---

## Cleanup
Delete the `lab-07-ingress-tls` namespace. This removes the Ingresses, the `Issuer`/`Certificate`/TLS Secret, both backends, the cache, and the loadgen pods. The shared `ingress-nginx-controller` LoadBalancer lives in the `ingress-nginx` namespace and is *not* touched here - it is uninstalled in cluster-setup teardown, which releases the cloud LB. cert-manager (cluster-scoped CRDs + controllers) likewise stays installed for other labs.

### EKS
The controller's external address is an **NLB hostname (`...elb.amazonaws.com`); resolving the lab hostname against it works. Consider the alternative: the AWS Load Balancer Controller can satisfy an `Ingress` with `ingressClassName: alb` by provisioning an ALB** directly (L7 at the cloud edge, no in-cluster nginx). This lab uses ingress-nginx so the manifests are identical on OVH - see the lecture's DevOps note.

### OVH
The controller's external address is an **IP** from an OVH Load Balancer; resolving the lab hostname against that IP works the same way. There is no ALB-equivalent managed L7 ingress - ingress-nginx (or another in-cluster controller) is the path. The billed resource is the OVH LB fronting the controller.

---
*Stuck or finished? -> [`solution.md`](solution.md) for the worked commands & answers, then [`lecture.md`](lecture.md) for the course.*
