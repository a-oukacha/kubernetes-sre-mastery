# Lab 06 - Services, Endpoints & cluster DNS · **Exercise**
**Patterns:** Service Discovery **Source: KIA 5; KP "Service Discovery"; DDS "Replicated Load-Balanced Services" Est:** 55 min

> **This is the exercise - the commands are deliberately *not* given.** Your job is to work out the
> `kubectl` invocations yourself; that *is* the skill. Attempt every task and write down your answer to
> every **Predict / Observe / Prove it / Break it** before peeking. When you're stuck or done, [`solution.md`](solution.md) has the
> exact commands + the output you should have seen + every checkpoint answer. Then read
> [`lecture.md`](lecture.md) for the course.

## Objective
Expose and discover workloads **inside the cluster (ClusterIP, headless) and outside it (NodePort, optional LoadBalancer), and internalize the one fact that makes Services reliable: readiness - not "is the pod running" - decides whether a pod is in a Service's endpoints.** You'll watch traffic load-balance across endpoints, watch endpoint membership track replicas and readiness in real time, and reproduce the two ways a Service goes silently dark.

## Concepts exercised
- `ClusterIP` (the default VIP), `NodePort`, `LoadBalancer`, headless (`clusterIP: None`)
- `Endpoints` / `EndpointSlice` - the live list of pod IPs behind a Service
- Readiness gates EndpointSlice membership (the load-bearing fact)
- CoreDNS names: `<svc>.<ns>.svc.cluster.local`; headless = per-pod A records vs one VIP
- kube-proxy (iptables/IPVS) DNAT from the Service VIP to a chosen endpoint
- selector/label match as the join between a Service and its pods

## Prerequisites
- Labs 01-03 done (labels & selectors, the declarative model, `describe`/events; readiness probes from lab 03).
- A reachable cluster (`kubectl get nodes` -> 2-3 `Ready`). See `../00-cluster-setup/`.

## Setup
Create a namespace **`lab-06-services-dns`** and make it the default for the rest of this lab. Then bring up the lab's fixtures from the provided manifests: the `backend` Deployment (`manifests/backend.yaml` - 3x agnhost, readiness gated by a `/tmp/ready` sentinel file), its `ClusterIP` Service (`manifests/svc-clusterip.yaml`, named `backend`), and a busybox `client` pod (`manifests/client.yaml`) you'll use for in-cluster DNS lookups and HTTP. Wait until the Deployment reports 3/3 Ready and the client pod is Ready before continuing.

The ClusterIP / headless / NodePort / DNS steps **cost nothing - they're the core of the lab. The LoadBalancer step (Step 7) is optional** and creates a **billed cloud LB**; it's clearly marked and torn down in Cleanup.

**Predict (0):** The `backend` Service selects `app.kubernetes.io/name=backend`. How many pod IPs do you expect behind it right now? Inspect the EndpointSlice for the `backend` Service and confirm.

---

## Steps

### 1. Resolve the Service by DNS from inside the cluster
From the `client` pod, resolve the Service `backend` by its short name and by its fully-qualified name `backend.lab-06-services-dns.svc.cluster.local`. Separately, read the `backend` Service's own `clusterIP` from its spec.

**Observe (1):** Compare the IP the short name resolves to against the `clusterIP` - are they the same? The bare name `backend` expanded to the FQDN: inspect the client's `/etc/resolv.conf` and identify which DNS search-domain made that expansion happen.

### 2. Watch traffic load-balance across the 3 endpoints
The agnhost `/hostname` endpoint returns the serving **pod name**, so repeatedly fetching it through the Service reveals which backend answered each request. Send a dozen requests at the `backend` Service from the client and record the pod names returned.

**Predict (2):** You sent 12 requests to **one** Service VIP. How many *distinct* pod names do you expect across the 12 lines, and will they alternate in a strict round-robin order or look random? Run it and count the distinct names.

### 3. Endpoints track replica count - scale up, then down
Read the addresses currently in the `backend` EndpointSlice. Scale the Deployment to 5 replicas, wait for the rollout, and re-read the slice. Then scale back to 3.

**Prove it (3):** Capture the number of `addresses` in the EndpointSlice at 3 replicas, then at 5, then back at 3. Prove the endpoint count *equals the number of Ready pods* at each step - not the number of pods that merely exist. (Re-run the Step 2 request loop at 5 replicas; you should now see up to 5 distinct names.)

### 4. Kill a pod - watch its endpoint vanish and return
Set up two terminals. In terminal A, keep a live watch on the `backend` EndpointSlice. In terminal B, delete a single one of the backend pods and let the Deployment replace it.

**Observe (4): In terminal A you should see the dying pod's IP removed from the slice, then a new pod's IP added** once the Deployment's replacement passes readiness. During the brief gap, was the Service ever down? Re-run the Step 2 request loop while the replacement is starting and see whether any request fails.

### 5. Headless Service - DNS returns ALL pod IPs, not one VIP
Apply the headless Service `manifests/svc-headless.yaml` (named `backend-headless`). Confirm from its listing that it has no cluster VIP, then resolve `backend-headless` by DNS from the client.

**Predict (5):** `backend` (ClusterIP) resolved to **one** IP. The `backend-headless` Service has `clusterIP: None` and selects the same 3 pods. How many `Address:` lines do you expect the headless lookup to return, and whose IPs are they - a VIP, or the pods'? Compare the addresses to the backend pods' own IPs.

### 6. NodePort - reach the app from outside, on `<node>:<nodePort>`
Apply the NodePort Service `manifests/svc-nodeport.yaml` (named `backend-nodeport`) and read which node port it allocated. Find a node's IP address, then reach the app at that node IP on the allocated node port - from your laptop if the node and firewall allow it, and (always) from inside the cluster via the client. Use the `/hostname` endpoint so each response names the pod that served it.

**Observe (6):** You hit **one node's IP on its node port. Did every response come from a backend pod on that** node, or from pods across all nodes? (Compare the hostnames you got back against the backend pods and the node each one runs on.) This tells you whether the node you contacted forwarded traffic cluster-wide.

### 7. (OPTIONAL - COSTS MONEY) LoadBalancer - a real cloud LB
> **COST GUARD.** Applying this provisions a **real, billed cloud load balancer (one LB per Service). It is optional; the lab is complete without it. If you do it, delete it** (Cleanup does). The steps above cost nothing.

Pick your cloud, put the right provider annotations into `manifests/svc-loadbalancer.yaml` (Service `backend-lb`), apply it, wait for its `EXTERNAL-IP` to move from `<pending>` to a real address, and then reach the app's `/hostname` through that external address.

#### EKS
Requires the **AWS Load Balancer Controller (see `../00-cluster-setup/eks.md §2.4`). Configure the Service so the controller provisions an NLB** (L4) that targets pod IPs directly, internet-facing (the relevant `service.beta.kubernetes.io/aws-load-balancer-*` annotations control type `external`, NLB target-type `ip`, and scheme). The external address will be an `*.elb.amazonaws.com` hostname; allow a minute or two for the NLB and DNS to come up.

#### OVH
Uses the OVH cloud-controller to provision an **OVH Load Balancer** with a public IP (**billed**). Optionally tune it via the OVH proxy-protocol annotation. The external address will be a public IP.

**Predict (7):** A `LoadBalancer` Service is a `NodePort` Service with a cloud LB bolted in front. After it's ready and you inspect `backend-lb` - do you expect it to *also* show a `NodePort` in its `PORT(S)` column? And does it have its own `clusterIP` too?

---

## Verify
Demonstrate success with observable signals: DNS resolves the `backend` ClusterIP Service from the client; a batch of requests to `backend/hostname` is spread across **all 3 pod names (every pod gets a share); the `backend` EndpointSlice holds exactly 3 addresses; and the headless lookup of `backend-headless` returns 3** pod IPs.

yes Success = DNS resolves; the requests are spread across **all 3 pod names (each count > 0); the EndpointSlice holds 3 addresses; headless `nslookup` returns 3** pod IPs.

---

## Break it - "Service up, but nothing answers" (both ways)
A Service object can be perfectly healthy and still send every client into the void. There are two distinct causes; reproduce both.

### Break-it A - all backends NotReady -> endpoints drain to empty
Flip every backend pod NotReady by removing its readiness sentinel (the probe checks for `/tmp/ready`). Then confirm the pods are still `Running` but `0/1 READY`, and inspect the `backend` EndpointSlice.

**Predict (B1):** The pods are still `Running` (the process is alive, liveness still passes) but now `0/1 READY`. How many addresses remain in the EndpointSlice? Now make a request to the `backend` Service from the client and predict the result - what error does the client get, and does the `backend` Service object itself report anything wrong when you inspect or describe it?

**Now heal it** - recreate the sentinel on every backend pod and confirm the EndpointSlice repopulates to 3 addresses.

### Break-it B - selector typo -> endpoints empty from birth, zero errors anywhere
This is the silent classic. Apply `manifests/svc-mismatch.yaml` (Service `backend-typo`), whose selector targets `app=backend` while the pods actually carry `app.kubernetes.io/name=backend`. Confirm the Service got a ClusterIP and looks healthy, inspect its EndpointSlice, and make a request to it from the client.

**Predict (B2):** DNS *will* resolve `backend-typo` (it has a VIP). So what happens to the request - and where, if anywhere, does Kubernetes tell you the cause? Compare the `Endpoints:` / `Selector:` lines when you describe `backend-typo` against the working `backend`.

**Fix it by aligning the selector** to the pods' real label (`app.kubernetes.io/name=backend`), then confirm the EndpointSlice fills with 3 addresses and the Service now answers.

**Prove it (B3): A one-line selector change took `backend-typo` from "silently broken" to "serving" with no pod restart. State the single command** you'd run *first* to diagnose either failure (A or B) - the one that distinguishes "no pods" from "pods not Ready" from "selector matches nothing."

---

## Cleanup
If you did the **OPTIONAL Step 7**, delete the cloud LB Service **first** so the controller releases the load balancer before the namespace goes away (this avoids an orphaned, still-billed LB); confirm it is gone before continuing. Then delete the `lab-06-services-dns` namespace, which removes everything else (Deployment, Services, client, EndpointSlices).

The ClusterIP/headless/NodePort/DNS objects cost nothing; deleting the namespace is enough for them. Only the LoadBalancer Service incurs cloud cost - confirm `backend-lb` is gone cluster-wide before you walk away.

---
*Stuck or finished? -> [`solution.md`](solution.md) for the worked commands & answers, then [`lecture.md`](lecture.md) for the course.*
