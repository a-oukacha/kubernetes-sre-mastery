# Lab 01 - Cluster fundamentals & kubectl fluency · **Exercise**
**Patterns:** - (foundations) **Source:** KIA 2-3 **Est:** 45 min

> **This is the exercise - the commands are deliberately *not* given.** Your job is to work out the
> `kubectl` invocations yourself; that *is* the skill. Attempt every task and write down your answer to
> every **Predict / Observe / Prove it** before peeking. When you're stuck or done, [`solution.md`](solution.md) has the
> exact commands + the output you should have seen + every checkpoint answer. Then read
> [`lecture.md`](lecture.md) for the course.

## Objective
Operate the cluster confidently and internalize the two ideas everything else builds on: **the declarative model (you describe desired state; controllers make it real) and label-driven selection** (labels are the join key the whole system uses).

## Concepts exercised
- kubeconfig, contexts, namespaces
- Pods - imperative `kubectl run` vs declarative YAML
- labels & annotations; label selectors
- `get` / `describe` / `logs` / `exec` / `get events`
- `kubectl debug` (ephemeral container)
- `--dry-run=client -o yaml` and `kubectl explain`

## Prerequisites
- A reachable cluster (2-3 `Ready` nodes). See `../00-cluster-setup/`.
- No prior labs.

## Setup
Create a namespace **`lab-01-fundamentals`** and make it the default for the rest of this lab (so you don't have to pass `-n` every time).

**Predict (0):** You just created the namespace. Does it contain any pods yet? Check, and see if you were right.

---

## Tasks

### 1. Where am I? Contexts & config
Find out: which **context** kubectl is currently using; the **cluster / user / namespace** that context maps to; and what **other contexts** you could switch to.

**Observe (1):** Which single field in your context's config is what makes commands default to `lab-01-fundamentals`?

### 2. Create a Pod imperatively
Imperatively run one pod named **`hello`** from image `registry.k8s.io/e2e-test-images/agnhost:2.47` with args `netexec --http-port=8080`, exposing port 8080. Watch it come up (don't just snapshot once - watch the status change).

**Predict (2):** Before it settles, what `STATUS` values will you pass through, and in what order?

### 3. Turn imperative into declarative (the move you'll use forever)
You rarely keep hand-run pods. Produce the YAML for a pod without actually creating it - i.e. render the manifest a `kubectl run` *would* have created, but create nothing. (Hint: there's a flag for "render, don't apply," plus an output-format flag.)

**Observe (3):** Confirm nothing was created. Why is this "render-only" trick the right way to start a manifest you'll commit to Git?

### 4. Apply the lab's declarative pods
Apply the two provided manifests `manifests/pod-blue.yaml` and `manifests/pod-green.yaml`, then list the pods **with their labels shown**.

**Predict (4): Open both YAMLs. They share `app: demo` but differ in `color:`. A selector for `app=demo` will match how many** of the two?

### 5. Label-driven selection (the core skill)
Select pods by label in a few ways: everything with `app=demo`; just `color=blue`; the set where `color` is `blue` *or* `green`; and finally list pods showing `color` as its own column.

**Prove it (5): Add a label `tier=frontend` to the running** `echo-green` pod (no respec, no restart), then select on `tier=frontend`. Convince yourself `echo-green` now appears and `echo-blue` does not - you changed an object's set-membership live.

### 6. Read the object: describe, logs, exec, events
For `echo-blue`: describe it, read its logs, exec into it and curl its own `/hostname` endpoint on `localhost:8080`, and view the cluster events newest-last.

**Observe (6): In `describe`, find the `Events:` block. List the event `Reason`s** between scheduling and running. Memorize this sequence - it's your debugging map.

### 7. `kubectl explain` - the schema is self-documenting
Use the built-in schema explorer to read the spec for a Pod's container `resources`, and to dump the Pod spec recursively.

**Observe (7):** Note you never have to memorize field names. Where did this schema come from, and how does it relate to the render-only YAML from task 3?

---

## Verify
Demonstrate success with observable signals: both `demo` pods `Running` and selectable by label; and `echo-blue`'s `status.phase` reads `Running`.

yes Success = two `demo` pods Running and selectable by label.

---

## Break it - see a real failure and debug it the right way
Apply `manifests/pod-broken.yaml` (its image tag doesn't exist) and watch the status churn.

**Predict (B1):** What `STATUS` will `echo-broken` settle into - and will it *ever* reach Running?

Now debug it the way you always should - events first, not logs. Pull up just the events for `echo-broken`.

**Observe (B2):** Which event `Reason` explains the failure? Why would `kubectl logs` be useless here?

**Bonus - ephemeral debug container.** Attach a throwaway `busybox:1.36` debug container to the *running* `echo-blue` (targeting its `echo` container) and curl `localhost:8080/` from inside.

**Observe (B3): Confirm the debug container was added to the live pod. Did the original `echo` container restart**? (Check `RESTARTS`.)

---

## Cleanup
Delete the `lab-01-fundamentals` namespace. No cloud LB/volume was created here, so that's enough.

---
*Stuck or finished? -> [`solution.md`](solution.md) for the worked commands & answers, then [`lecture.md`](lecture.md) for the course.*
