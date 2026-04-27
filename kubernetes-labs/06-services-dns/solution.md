# Lab 06 - Services, Endpoints & cluster DNS · **Solution**
**Patterns:** Service Discovery **Source: KIA 5; KP "Service Discovery"; DDS "Replicated Load-Balanced Services" Est:** 55 min

> The worked lab, with every command. Try the [exercise](lab.md) first; the checkpoint answers and the
> *why* are in [`lecture.md`](lecture.md).

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
```bash
kubectl create namespace lab-06-services-dns
kubens lab-06-services-dns          # or add -n lab-06-services-dns to every command

kubectl apply -f manifests/backend.yaml        # Deployment: 3x agnhost, readiness via /tmp/ready sentinel
kubectl apply -f manifests/svc-clusterip.yaml  # ClusterIP Service "backend"
kubectl apply -f manifests/client.yaml         # busybox client pod for nslookup/wget
kubectl rollout status deployment/backend      # blocks until 3/3 Ready
kubectl wait --for=condition=Ready pod/client --timeout=60s
```
The ClusterIP / headless / NodePort / DNS steps **cost nothing - they're the core of the lab. The LoadBalancer step (Step 7) is optional** and creates a **billed cloud LB**; it's clearly marked and torn down in Cleanup.

**Predict (0):** The `backend` Service exists and selects `app.kubernetes.io/name=backend`. How many pod IPs do you expect behind it right now? Check with `kubectl get endpointslices -l kubernetes.io/service-name=backend`.

---

## Steps

### 1. Resolve the Service by DNS from inside the cluster
```bash
kubectl exec client -- nslookup backend
kubectl exec client -- nslookup backend.lab-06-services-dns.svc.cluster.local
kubectl get svc backend -o jsonpath='{.spec.clusterIP}'; echo
```
**Observe (1):** `nslookup backend` resolves to a single IP. Compare it to the `clusterIP` you just printed - are they the same? Note that the short name `backend` worked: which DNS search-domain (in `/etc/resolv.conf` of the client) let the bare name expand to the FQDN? Run `kubectl exec client -- cat /etc/resolv.conf`.

### 2. Watch traffic load-balance across the 3 endpoints
`/hostname` on agnhost returns the **pod name**, so hammering the Service shows you which backend served each request:
```bash
kubectl exec client -- sh -c 'for i in $(seq 1 12); do wget -qO- backend/hostname; echo; done'
```
**Predict (2):** You sent 12 requests to **one** Service VIP. How many *distinct* pod names do you expect to see across the 12 lines, and will they alternate in a strict round-robin order or look random? Run it and count the distinct names.

### 3. Endpoints track replica count - scale up, then down
```bash
kubectl get endpointslices -l kubernetes.io/service-name=backend -o wide
kubectl scale deployment/backend --replicas=5
kubectl rollout status deployment/backend
kubectl get endpointslices -l kubernetes.io/service-name=backend -o wide   # count the addresses
kubectl scale deployment/backend --replicas=3
```
**Prove it (3):** Capture the number of `addresses` in the EndpointSlice at 3 replicas, then at 5, then back at 3. Prove the endpoint count *equals the number of Ready pods* at each step - not the number of pods that merely exist. (Re-run the Step 2 `wget` loop at 5 replicas; you should now see up to 5 distinct names.)

### 4. Kill a pod - watch its endpoint vanish and return
In terminal A, watch the slice; in terminal B, delete a pod:
```bash
# terminal A - leave running:
kubectl get endpointslices -l kubernetes.io/service-name=backend -w
```
```bash
# terminal B:
kubectl delete pod -l app.kubernetes.io/name=backend --field-selector status.phase=Running | head -1
# (delete just one: )
POD=$(kubectl get pod -l app.kubernetes.io/name=backend -o jsonpath='{.items[0].metadata.name}')
kubectl delete pod "$POD"
```
**Observe (4): In terminal A you see the dying pod's IP removed from the slice, then a new pod's IP added** once the Deployment replaces it and the replacement passes readiness. During the brief gap, was the Service ever down? Re-run the Step 2 `wget` loop while the replacement is starting and see whether any request fails.

### 5. Headless Service - DNS returns ALL pod IPs, not one VIP
```bash
kubectl apply -f manifests/svc-headless.yaml
kubectl get svc backend-headless                     # note CLUSTER-IP column says "None"
kubectl exec client -- nslookup backend-headless
```
**Predict (5):** `backend` (ClusterIP) resolved to **one** IP. The `backend-headless` Service has `clusterIP: None` and selects the same 3 pods. How many `Address:` lines do you expect `nslookup backend-headless` to return, and whose IPs are they - a VIP, or the pods'? Compare the addresses to `kubectl get pods -l app.kubernetes.io/name=backend -o wide`.

### 6. NodePort - reach the app from outside, on `<node>:<nodePort>`
```bash
kubectl apply -f manifests/svc-nodeport.yaml
kubectl get svc backend-nodeport                     # PORT(S) shows 80:30806/TCP
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}')
[ -z "$NODE_IP" ] && NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
echo "node: $NODE_IP"
# from your laptop if the node is reachable / firewall allows 30806:
curl -s "http://$NODE_IP:30806/hostname"; echo
# always works from inside the cluster (proves the node port even if your firewall blocks it):
kubectl exec client -- sh -c "for i in 1 2 3 4 5 6; do wget -qO- $NODE_IP:30806/hostname; echo; done"
```
**Observe (6):** You hit **one node's IP on port `30806`. Did every response come from a backend pod on that** node, or from pods across all nodes? (Compare the hostnames you got back with `kubectl get pods -l app.kubernetes.io/name=backend -o wide` and the node each pod runs on.) This tells you whether the node you contacted forwarded traffic cluster-wide.

### 7. (OPTIONAL - COSTS MONEY) LoadBalancer - a real cloud LB
> **COST GUARD.** Applying this provisions a **real, billed cloud load balancer (one LB per Service). It is optional; the lab is complete without it. If you do it, delete it** (Cleanup does). The steps above cost nothing.

Pick your cloud, edit the annotations into `manifests/svc-loadbalancer.yaml`, then apply.

#### EKS
Requires the **AWS Load Balancer Controller (see `../00-cluster-setup/eks.md §2.4`). Provisions an NLB** (L4) that targets pod IPs directly:
```yaml
# metadata.annotations in svc-loadbalancer.yaml:
service.beta.kubernetes.io/aws-load-balancer-type: external
service.beta.kubernetes.io/aws-load-balancer-nlb-target-type: ip
service.beta.kubernetes.io/aws-load-balancer-scheme: internet-facing   # use "internal" for an internal NLB
```
```bash
kubectl apply -f manifests/svc-loadbalancer.yaml
kubectl get svc backend-lb -w        # EXTERNAL-IP goes <pending> -> an *.elb.amazonaws.com hostname
LB=$(kubectl get svc backend-lb -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
curl -s "http://$LB/hostname"; echo  # may take 1-2 min for the NLB + DNS to come up
```

#### OVH
Uses the OVH cloud-controller; provisions an **OVH Load Balancer** with a public IP (**billed**):
```yaml
# metadata.annotations in svc-loadbalancer.yaml (optional tuning):
service.beta.kubernetes.io/ovh-loadbalancer-proxy-protocol: "v2"
```
```bash
kubectl apply -f manifests/svc-loadbalancer.yaml
kubectl get svc backend-lb -w        # EXTERNAL-IP goes <pending> -> a public IP
LB=$(kubectl get svc backend-lb -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
curl -s "http://$LB/hostname"; echo
```
**Predict (7):** A `LoadBalancer` Service is a `NodePort` Service with a cloud LB bolted in front. After it's ready, run `kubectl get svc backend-lb` - do you expect it to *also* show a `NodePort` in its `PORT(S)` column? And does it have its own `clusterIP` too?

---

## Verify
```bash
# DNS resolves the ClusterIP Service:
kubectl exec client -- nslookup backend >/dev/null && echo "DNS OK"

# load balancing: multiple DISTINCT pod names across requests:
kubectl exec client -- sh -c 'for i in $(seq 1 20); do wget -qO- backend/hostname; echo; done' | sort | uniq -c

# endpoint count tracks Ready replicas (expect 3 addresses):
kubectl get endpointslices -l kubernetes.io/service-name=backend \
  -o jsonpath='{range .items[*]}{.endpoints[*].addresses[0]}{"\n"}{end}' | wc -l

# headless returns N pod IPs (expect 3):
kubectl exec client -- nslookup backend-headless | grep -c '^Address: '
```
yes Success = DNS resolves; the 20 requests are spread across **all 3 pod names (each count > 0); the EndpointSlice holds 3 addresses; headless `nslookup` returns 3** pod IPs.

---

## Break it - "Service up, but nothing answers" (both ways)
A Service object can be perfectly healthy and still send every client into the void. There are two distinct causes; reproduce both.

### Break-it A - all backends NotReady -> endpoints drain to empty
Flip every backend pod NotReady by removing its readiness sentinel (the probe is `test -f /tmp/ready`):
```bash
for p in $(kubectl get pod -l app.kubernetes.io/name=backend -o name); do
  kubectl exec "$p" -- rm -f /tmp/ready
done
sleep 5
kubectl get pods -l app.kubernetes.io/name=backend          # Running but 0/1 READY
kubectl get endpointslices -l kubernetes.io/service-name=backend -o wide
```
**Predict (B1):** The pods are still `Running` (the process is alive, liveness still passes) but now `0/1 READY`. How many addresses are in the EndpointSlice? Now `wget` the Service from the client and predict the result:
```bash
kubectl exec client -- sh -c 'wget -T 5 -qO- backend/hostname || echo "FAILED: $?"'
```
What error does the client get, and does the `backend` Service object itself report anything wrong in `kubectl get svc backend` / `kubectl describe svc backend`?

**Now heal it** - recreate the sentinel and watch endpoints repopulate:
```bash
for p in $(kubectl get pod -l app.kubernetes.io/name=backend -o name); do
  kubectl exec "$p" -- touch /tmp/ready
done
sleep 5
kubectl get endpointslices -l kubernetes.io/service-name=backend -o wide   # 3 addresses back
```

### Break-it B - selector typo -> endpoints empty from birth, zero errors anywhere
This is the silent classic. The Service looks fine; its selector just doesn't match any pod's labels:
```bash
kubectl apply -f manifests/svc-mismatch.yaml      # selector: app=backend (pods use app.kubernetes.io/name=backend)
kubectl get svc backend-typo                       # has a ClusterIP, looks totally healthy
kubectl get endpointslices -l kubernetes.io/service-name=backend-typo -o wide
kubectl exec client -- sh -c 'wget -T 5 -qO- backend-typo/hostname || echo "FAILED: $?"'
```
**Predict (B2):** DNS *will* resolve `backend-typo` (it has a VIP). So what happens to the `wget` - and where, if anywhere, does Kubernetes tell you the cause? Compare `kubectl describe svc backend-typo` (look at its `Endpoints:`/`Selector:` lines) against the working `backend`.

**Fix it by aligning the selector** to the pods' real label:
```bash
kubectl patch svc backend-typo -p '{"spec":{"selector":{"app.kubernetes.io/name":"backend"}}}'
kubectl get endpointslices -l kubernetes.io/service-name=backend-typo -o wide   # 3 addresses appear
kubectl exec client -- wget -qO- backend-typo/hostname; echo                    # now answers
```
**Prove it (B3): A one-line selector change took `backend-typo` from "silently broken" to "serving" with no pod restart. State the single command** you'd run *first* to diagnose either failure (A or B) - the one that distinguishes "no pods" from "pods not Ready" from "selector matches nothing."

---

## Cleanup
```bash
# If you did the OPTIONAL Step 7, delete the cloud LB FIRST so the controller
# releases it before the namespace goes away (avoids an orphaned, billed LB):
kubectl delete -f manifests/svc-loadbalancer.yaml --ignore-not-found
kubectl get svc backend-lb 2>/dev/null && echo "LB still deleting - wait until gone"

# Then the namespace removes everything else (Deployment, Services, client, EndpointSlices):
kubectl delete namespace lab-06-services-dns
```
The ClusterIP/headless/NodePort/DNS objects cost nothing; deleting the namespace is enough for them. Only the LoadBalancer Service incurs cloud cost - confirm `backend-lb` is gone (`kubectl get svc -A | grep backend-lb` returns nothing) before you walk away.

---
*Now read [`lecture.md`](lecture.md) and grade your Predict predictions.*
