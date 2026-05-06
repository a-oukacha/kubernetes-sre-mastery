# Lab 12 - Init containers & Sidecar · **Exercise**
**Patterns:** Init Container + Sidecar **Source: KIA 17; KP "Init Container"/"Sidecar"; DDS "Sidecar" Est:** 50 min

> **This is the exercise - the commands are deliberately *not* given.** Your job is to work out the
> `kubectl` invocations yourself; that *is* the skill. Attempt every task and write down your answer to
> every **Predict / Observe / Prove it / Break it** before peeking. When you're stuck or done, [`solution.md`](solution.md) has the
> exact commands + the output you should have seen + every checkpoint answer. Then read
> [`lecture.md`](lecture.md) for the course.

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
- A reachable cluster (2-3 `Ready` nodes).
- For the **native sidecar** step: Kubernetes **≥ 1.29** (GA). On ≥1.28 it works behind the `SidecarContainers` feature gate; on managed clusters check the control-plane version.
- No cloud add-ons - everything here is a single pod with an `emptyDir`.

## Setup
Create a namespace **`lab-12-init-sidecar`** and make it the default for the rest of this lab (so you don't have to pass `-n` every time).

**Predict (0):** Open `manifests/pod-init-app.yaml`. It has one `initContainers[]` entry (`seed`) and one `containers[]` entry (`app`). When you apply it, in which order do the two containers run - together, or one fully before the other? Which one writes `/work/index.html`, and which one serves it?

---

## Tasks

### 1. Deploy the init+app pod and watch init gate the app
Apply `manifests/pod-init-app.yaml` and watch the `init-app` pod's status evolve in real time (don't snapshot once - watch it transition) until it settles `Running` with `READY 1/1`.

**Observe (1):** In the `STATUS` column, what value appears *first*, before the app container exists? Trace the progression from that first value through to `Running`. At what point does the app container actually appear?

**Prove it (2):** Demonstrate that the HTML the app serves was placed there by the init container, not baked into the app image. Fetch `/index.html` over `localhost` from inside the `app` container, read the `seed` init container's log, and inspect `.status.initContainerStatuses` to confirm the init reports `terminated=Completed`. The proof is that the app serves a file it never shipped.

### 2. Read the ordering off the live object
Pull up the pod's event stream and use it as the source of truth for container startup ordering.

**Observe (3):** In the events, find the `Created`/`Started` entries for the `seed` container versus the `app` container. Which container's `Started` event comes first, and is there any overlap between them?

### 3. Break it Break the init - see the app never start
Apply `manifests/pod-init-broken.yaml` (its init container does `exit 1`) and watch the `init-broken` pod until its status settles.

**Predict (B1):** What `STATUS` will `init-broken` settle into, will `RESTARTS` climb, and will the `app` container ever be created?

**Observe (B2):** Debug it events-first - the app never ran, so app logs don't exist. Look at the settled status, the event stream, the failing init's log, and the pod's `.status.containerStatuses`. What does the app's `containerStatuses` contain, and what does that prove about whether the app container was ever created?

Prove it (B3) - confirm by contrast. Compare `.status.containerStatuses` for the healthy `init-app` pod against the broken `init-broken` pod. Show that `init-app` reports its app `started=true` while `init-broken` reports no app container at all. Then delete the broken pod before continuing.

### 4. Add a CLASSIC sidecar - concurrent, no ordering
Apply `manifests/pod-classic-sidecar.yaml` and watch `classic-sidecar` until it is `Running`. This pod adds a second `containers[]` entry (`refresh`) that rewrites `/work/index.html` every 5 seconds, alongside the original init (`seed`) and app.

**Predict (4):** Will the served content stay frozen at the init's seed, or change over time? When is the pod considered `READY` - `1/2` or `2/2`?

**Observe (5):** Fetch `/index.html` a few times a few seconds apart, read the `refresh` container's log, and check the pod's `READY` column. Does the served content change between fetches? What `READY` value does the pod report, and why?

Prove it (6) - classic sidecar crash semantics. Record each container's restart count as a baseline, then kill the **app** process (its PID 1) from inside the `app` container, and watch what each container does as the pod recovers.

**Predict (7): When the app's PID 1 dies, does the whole pod** restart, or just the `app` container? Does the `refresh` sidecar keep running with its log/timestamp uninterrupted, or does it restart too?

### 5. Swap in a NATIVE sidecar - ordered start, drains last
Apply `manifests/pod-native-sidecar.yaml` and watch `native-sidecar` until it is `Running`. Here the `refresh` helper has been moved into `initContainers[]` with `restartPolicy: Always`.

**Predict (8):** Does the `refresh` sidecar start **before** or **after** the `app` container? And why is this pod `READY 1/1` (not `2/2`) even though it runs three containers' worth of work?

**Observe (9):** Read the startup ordering from the events (the `Started`/`Created` entries), read the `refresh` container's log, and inspect `.status.initContainerStatuses`. Does `refresh`'s `Started` event come before or after the `app`'s? Under which status list does `refresh` appear, and what is its state - `running` or `terminated`?

Prove it (10) - it serves like the classic one, but with ordering. Fetch `/index.html` twice a few seconds apart and confirm the content is being refreshed (the timestamp advances) - the same serving behavior as the classic version, but now you have proof (from step 9) that the helper was guaranteed up *before* the app, which the classic version could not promise.

### EKS
- Native sidecars (`initContainers` + `restartPolicy: Always`) are **GA in 1.29** and on by default. EKS control planes on 1.29+ support them with no flags; on 1.28 the `SidecarContainers` gate is on by default but treat it as beta. Check your control-plane version; if your cluster is <1.28, skip step 5's native version (the classic version still runs everywhere).

### OVH
- OVH Managed Kubernetes (MKS) tracks upstream minors; on 1.29+ native sidecars are GA and need no configuration. Verify your control-plane version and, if you are below 1.28, run only the classic-sidecar version. Nothing cloud-specific is created in this lab (no LB, no volume).

---

## Verify
Demonstrate success with observable signals: (1) `init-app` serves the init-seeded HTML over `localhost` and reports `terminated=Completed` for the init plus `app started=true`; (2) the `classic-sidecar` pod returns a *newer* timestamp on a second fetch a few seconds later; and (3) the `native-sidecar` pod shows `refresh` in a `running` state under `initContainerStatuses`.

yes Success = init-seeded HTML served with init `Completed` and app `started=true`; classic-sidecar timestamp advancing; native-sidecar `refresh` running under `initContainerStatuses`.

---

## Break it - failing init keeps the app from ever starting
(Done inline in **Step 3 above - the `init-broken` pod.) The takeaway: an init container that `exit 1`s leaves the pod cycling `Init:Error` -> `Init:CrashLoopBackOff`, the app container is never created (`.status.containerStatuses` is empty), and no amount of waiting helps - only fixing the init does. Contrast this with the classic sidecar in Step 4 step 6, where killing the app** restarts only that container and the pod recovers on its own.

---

## Cleanup
Delete the `lab-12-init-sidecar` namespace. No cloud LB/volume was created here (only an in-pod `emptyDir`), so that's enough.

---
*Stuck or finished? -> [`solution.md`](solution.md) for the worked commands & answers, then [`lecture.md`](lecture.md) for the course.*
