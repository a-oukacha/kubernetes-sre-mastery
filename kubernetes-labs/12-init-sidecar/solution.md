# Lab 12 - Init containers & Sidecar · **Solution**
**Patterns:** Init Container + Sidecar **Source: KIA 17; KP "Init Container"/"Sidecar"; DDS "Sidecar" Est:** 50 min

> The worked lab, with every command. Try the [exercise](lab.md) first; the checkpoint answers and the
> *why* are in [`lecture.md`](lecture.md).

## Objective
Compose a pod out of several containers and *watch* the two composition rules diverge: an **init container** runs to completion **before the app starts (ordered, must-succeed setup), while a sidecar** runs **alongside the app (a concurrent helper). Then see the difference between a classic sidecar (an extra `containers[]` entry, no lifecycle ordering) and a native** sidecar (an `initContainers[]` entry with `restartPolicy: Always`, GA in 1.29) that finally starts before the app and drains after it.

## Concepts exercised
- `initContainers` - ordered, run-to-completion; each must exit 0 before the next, and the app starts only after **all** of them succeed
- shared `emptyDir` between containers; one writes, another serves
- localhost network sharing inside a pod (all containers share the network namespace)
- classic sidecar = an extra `containers[]` entry - concurrent, **no** start/stop ordering vs the app
- native sidecar = an `initContainers[]` entry with `restartPolicy: Always` - starts **before the app, runs for the pod's life, terminates after** the app
- `Init:0/1` -> `PodInitializing` -> `Running` status progression
- the failure mode: a failing init container keeps the pod out of `Running` and the app container is never created

## Prerequisites
- Labs 01-03 done (you know `apply`, labels/selectors, `describe`+events, `get pod -o jsonpath`, `exec`).
- Lab 09 helpful (you've seen `emptyDir` shared between containers).
- A reachable cluster (`kubectl get nodes` -> 2-3 `Ready`).
- For the **native sidecar** step: Kubernetes **≥ 1.29** (GA). On ≥1.28 it works behind the `SidecarContainers` feature gate; on managed clusters check the control-plane version (`kubectl version`).
- No cloud add-ons - everything here is a single pod with an `emptyDir`.

## Setup
```bash
kubectl create namespace lab-12-init-sidecar
kubens lab-12-init-sidecar          # or add -n lab-12-init-sidecar to every command
```
**Predict (0):** Open `manifests/pod-init-app.yaml`. It has one `initContainers[]` entry (`seed`) and one `containers[]` entry (`app`). When you apply it, in which order do the two containers run - together, or one fully before the other? Which one writes `/work/index.html`, and which one serves it?

---

## Steps

### 1. Deploy the init+app pod and watch init gate the app
```bash
kubectl apply -f manifests/pod-init-app.yaml
kubectl get pod init-app -w        # watch ~20s; Ctrl-C once STATUS is Running and READY 1/1
```
**Observe (1): For the first moment the `STATUS` column reads `Init:0/1` (the init container is running and the app has not** been created), then it flips through `PodInitializing` to `Running` with `READY 1/1`. Note that the app never appears while `Init:0/1` is showing.

**Prove it (2):** The content the app serves came from the init container, not the app image:
```bash
kubectl exec init-app -c app -- wget -qO- localhost/index.html
kubectl logs init-app -c seed                       # the init container's log
kubectl get pod init-app -o jsonpath='{range .status.initContainerStatuses[*]}{.name}{" terminated="}{.state.terminated.reason}{"\n"}{end}'
```
You should see the init-seeded HTML come back over `localhost`, the `seed` log saying it wrote the file, and the init container reporting `terminated=Completed`. The app served a file it never shipped in its own image - the init put it there first.

### 2. Read the ordering off the live object
```bash
kubectl describe pod init-app | sed -n '/Events:/,$p'
```
**Observe (3):** In the event stream, find the order of `Created`/`Started` for the `seed` container versus the `app` container. Which container's `Started` event comes first, and is there any overlap?

### 3. Break it Break the init - see the app never start
```bash
kubectl apply -f manifests/pod-init-broken.yaml
kubectl get pod init-broken -w      # watch ~60s; Ctrl-C once STATUS settles
```
**Predict (B1):** The init container in this pod does `exit 1`. What `STATUS` will `init-broken` settle into, will `RESTARTS` climb, and will the `app` container ever be created?

**Observe (B2):** Debug it events-first (the app never ran, so app logs don't exist):
```bash
kubectl get pod init-broken                                                                       # STATUS Init:Error / Init:CrashLoopBackOff, READY 0/1
kubectl describe pod init-broken | sed -n '/Events:/,$p'
kubectl logs init-broken -c seed                                                                  # the failing init's stderr
kubectl get pod init-broken -o jsonpath='{"app container statuses: "}{.status.containerStatuses}{"\n"}'
```
The pod is stuck cycling `Init:Error` -> `Init:CrashLoopBackOff`; the init's restart count climbs; and `containerStatuses` for the **app** is empty - the app container was **never created**. This is the init guarantee made visible: a must-succeed setup step blocks the entire pod.

Prove it (B3) - confirm by contrast. The healthy pod from step 1 has a *populated* app status; the broken one does not:
```bash
kubectl get pod init-app     -o jsonpath='{.status.containerStatuses[0].name}{" started="}{.status.containerStatuses[0].started}{"\n"}'
kubectl get pod init-broken  -o jsonpath='{"broken app statuses len -> "}{.status.containerStatuses}{"\n"}'
```
`init-app` reports `app started=true`; `init-broken` reports no app container at all. Clean up the broken pod before continuing:
```bash
kubectl delete pod init-broken
```

### 4. Add a CLASSIC sidecar - concurrent, no ordering
```bash
kubectl apply -f manifests/pod-classic-sidecar.yaml
kubectl get pod classic-sidecar -w   # watch ~15s; Ctrl-C once READY 2/2 Running
```
**Predict (4):** This pod has an init (`seed`), the app, and a second `containers[]` entry (`refresh`) that rewrites `/work/index.html` every 5 seconds. Will the served content stay frozen at the init's seed, or change over time? When is the pod considered `READY` - `1/2` or `2/2`?

**Observe (5):** Watch the content change and read the sidecar's heartbeat:
```bash
for i in 1 2 3; do kubectl exec classic-sidecar -c app -- wget -qO- localhost/index.html; sleep 5; done
kubectl logs classic-sidecar -c refresh --tail=4
kubectl get pod classic-sidecar    # READY column
```
The served HTML now says "refreshed by the sidecar" and the timestamp advances every 5 s; the `refresh` log shows the periodic write; and the pod is `READY 2/2` (both `containers[]` count toward readiness).

Prove it (6) - classic sidecar crash semantics. Kill the **app** process and watch what each container does:
```bash
kubectl get pod classic-sidecar -o jsonpath='{range .status.containerStatuses[*]}{.name}{" restarts="}{.restartCount}{"\n"}{end}'   # baseline
kubectl exec classic-sidecar -c app -- /bin/sh -c 'kill 1' ; true
kubectl get pod classic-sidecar -w   # watch ~20s; Ctrl-C once app restarts and READY returns to 2/2
```
**Predict (7): When the app's PID 1 dies, does the whole pod** restart, or just the `app` container? Does the `refresh` sidecar keep running and keep its log/timestamp uninterrupted, or does it restart too?

### 5. Swap in a NATIVE sidecar - ordered start, drains last
```bash
kubectl apply -f manifests/pod-native-sidecar.yaml
kubectl get pod native-sidecar -w    # watch ~15s; Ctrl-C once READY 1/1 Running
```
**Predict (8): Here `refresh` was moved into `initContainers[]` with `restartPolicy: Always`. Does the `refresh` sidecar start before** or **after** the `app` container? And why is this pod `READY 1/1` (not `2/2`) even though it runs three containers' worth of work?

**Observe (9):** Read the startup ordering and where the sidecar lives in the spec:
```bash
kubectl describe pod native-sidecar | sed -n '/Events:/,$p' | grep -E 'Started|Created'
kubectl logs native-sidecar -c refresh --tail=3                       # the native sidecar's log (note: it's an initContainer name)
kubectl get pod native-sidecar -o jsonpath='{range .status.initContainerStatuses[*]}{.name}{" state="}{.state}{"\n"}{end}'
```
The `refresh` container's `Started` event comes **before** the `app` container's, the sidecar is listed under `initContainerStatuses` yet is in a `running` state (not `terminated`), and its log keeps refreshing the content the app serves.

Prove it (10) - it serves like the classic one, but with ordering:
```bash
for i in 1 2; do kubectl exec native-sidecar -c app -- wget -qO- localhost/index.html; sleep 5; done
```
Content says "refreshed by the native sidecar" and the timestamp advances - same serving behavior as the classic version, but you proved (step 9) the helper was guaranteed up *before* the app, which the classic version could not promise.

### EKS
- Native sidecars (`initContainers` + `restartPolicy: Always`) are **GA in 1.29** and on by default. EKS control planes on 1.29+ support them with no flags; on 1.28 the `SidecarContainers` gate is on by default but treat it as beta. Check `kubectl version`; if your cluster is <1.28, skip step 5's native version (the classic version still runs everywhere).

### OVH
- OVH Managed Kubernetes (MKS) tracks upstream minors; on 1.29+ native sidecars are GA and need no configuration. Verify with `kubectl version` and, if you are below 1.28, run only the classic-sidecar version. Nothing cloud-specific is created in this lab (no LB, no volume).

---

## Verify
```bash
# 1) the app serves INIT-seeded content over localhost (init ran first):
kubectl exec init-app -c app -- wget -qO- localhost/index.html
# 2) the init completed before the app started, and the app is started:
kubectl get pod init-app -o jsonpath='{.status.initContainerStatuses[0].state.terminated.reason}{" | app started="}{.status.containerStatuses[0].started}{"\n"}'
# 3) a sidecar keeps the content fresh (run twice, ~5s apart, timestamps differ):
kubectl exec classic-sidecar -c app -- wget -qO- localhost/index.html; sleep 5
kubectl exec classic-sidecar -c app -- wget -qO- localhost/index.html
# 4) the native sidecar is running (not terminated) under initContainerStatuses:
kubectl get pod native-sidecar -o jsonpath='{range .status.initContainerStatuses[*]}{.name}{"="}{.state}{"\n"}{end}'
```
yes Success = `init-app` serves the seeded HTML and reports `terminated=Completed | app started=true`; the classic-sidecar pod returns a *newer* timestamp on the second `wget`; and the native-sidecar pod shows `refresh` in a `running` state under `initContainerStatuses`.

---

## Break it - failing init keeps the app from ever starting
(Done inline in **Step 3 above - the `init-broken` pod.) The takeaway: an init container that `exit 1`s leaves the pod cycling `Init:Error` -> `Init:CrashLoopBackOff`, the app container is never created (`.status.containerStatuses` is empty), and no amount of waiting helps - only fixing the init does. Contrast this with the classic sidecar in Step 4 step 6, where killing the app** restarts only that container and the pod recovers on its own.

---

## Cleanup
```bash
kubectl delete namespace lab-12-init-sidecar
```
No cloud LB/volume was created in this lab (only an in-pod `emptyDir`) - deleting the namespace is enough.

---
*Done? Now read [`lecture.md`](lecture.md) and grade your Predict predictions.*
