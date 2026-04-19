# Lab 01 - Cluster fundamentals & kubectl fluency · **Solution**
**Patterns:** - (foundations) **Source:** KIA 2-3 **Est:** 45 min

> The worked lab, with every command. Try the [exercise](lab.md) first; the checkpoint answers and the
> *why* are in [`lecture.md`](lecture.md).

## Objective
Operate the cluster confidently and internalize the two ideas everything else builds on: **the declarative model (you describe desired state; controllers make it real) and label-driven selection** (labels are the join key the whole system uses).

## Concepts exercised
- kubeconfig, contexts, namespaces
- Pods - imperative `kubectl run` vs declarative YAML
- labels & annotations; label selectors (`-l`)
- `get` / `describe` / `logs` / `exec` / `get events`
- `kubectl debug` (ephemeral container)
- `--dry-run=client -o yaml` and `kubectl explain`

## Prerequisites
- A reachable cluster (`kubectl get nodes` -> 2-3 `Ready`). See `../00-cluster-setup/`.
- No prior labs.

## Setup
```bash
kubectl create namespace lab-01-fundamentals
kubens lab-01-fundamentals          # or add -n lab-01-fundamentals to every command
```
**Predict (0):** You just created a namespace. Does it contain any pods yet? Run `kubectl get pods` and check your guess.

---

## Steps

### 1. Where am I? Contexts and config
```bash
kubectl config current-context
kubectl config view --minify          # the cluster/user/namespace this context points at
kubectl config get-contexts           # all clusters you can talk to
```
**Observe (1):** Which field in `--minify` output decided that your commands now default to `lab-01-fundamentals`? (You set it with `kubens`.)

### 2. Create a Pod imperatively
```bash
kubectl run hello --image=registry.k8s.io/e2e-test-images/agnhost:2.47 \
  --port=8080 -- netexec --http-port=8080
kubectl get pods -o wide
```
**Predict (2):** Before running `get pods` - what `STATUS` values might you see in the first few seconds, and in what order? Watch with `kubectl get pods -w` (Ctrl-C to stop).

### 3. Turn imperative into declarative (the move you'll use forever)
You rarely keep imperative pods. Generate YAML *without creating anything* with `--dry-run=client`:
```bash
kubectl run hello2 --image=registry.k8s.io/e2e-test-images/agnhost:2.47 \
  --port=8080 --dry-run=client -o yaml -- netexec --http-port=8080
```
**Observe (3):** Nothing was created (`kubectl get pod hello2` -> NotFound). The same command minus `--dry-run` *would* create it. This `--dry-run=client -o yaml` trick is how you bootstrap manifests you then edit and commit.

### 4. Apply the lab's declarative pods
```bash
kubectl apply -f manifests/pod-blue.yaml
kubectl apply -f manifests/pod-green.yaml
kubectl get pods --show-labels
```
**Predict (4):** Open both YAMLs. They share `app: demo` but differ in `color:`. A selector for `app=demo` will match how many of these two pods?

### 5. Label-driven selection (the core skill)
```bash
kubectl get pods -l app=demo                 # selector
kubectl get pods -l color=blue
kubectl get pods -l 'color in (blue,green)'
kubectl get pods -L color                    # -L = show this label as a column (capital L)
```
**Prove it (5):** Add a label to a *running* pod and re-select:
```bash
kubectl label pod echo-green tier=frontend
kubectl get pods -l tier=frontend
```
You changed how a live object is *selected* without touching its spec. Convince yourself `echo-green` now appears and `echo-blue` does not.

### 6. Read the object: describe, logs, exec, events
```bash
kubectl describe pod echo-blue                # events are at the BOTTOM - read them
kubectl logs echo-blue
kubectl exec -it echo-blue -- /bin/sh -c 'wget -qO- localhost:8080/hostname; echo'
kubectl get events --sort-by=.lastTimestamp  # cluster-wide story, newest last
```
**Observe (6):** In `describe`, find the `Events:` block. List the event `Reason`s you see between scheduling and running (e.g. `Scheduled`, `Pulling`, `Pulled`, `Created`, `Started`). These are the lifecycle milestones - memorize this sequence; it's your debugging map.

### 7. `kubectl explain` - the schema is self-documenting
```bash
kubectl explain pod.spec.containers.resources
kubectl explain pod.spec --recursive | head -40
```
**Observe (7):** You never need to memorize field names - the API server ships its own schema. Note that this is the *same* schema the dry-run in step 3 produced.

---

## Verify
```bash
kubectl get pods -l app=demo            # echo-blue AND echo-green, both Running
kubectl get pod echo-blue -o jsonpath='{.status.phase}'; echo   # -> Running
```
yes Success = two `demo` pods Running and selectable by label.

---

## Break it - see a real failure and debug it the right way
```bash
kubectl apply -f manifests/pod-broken.yaml
kubectl get pods echo-broken -w          # watch the status churn; Ctrl-C after ~30s
```
**Predict (B1):** What `STATUS` will `echo-broken` settle into, and will it ever become Running?

Now debug it the way you always should - events first, not logs:
```bash
kubectl describe pod echo-broken | sed -n '/Events:/,$p'
```
**Observe (B2):** What event `Reason` explains the failure? (Logs would be useless here - the container never started. The *events* tell the story.)

**Bonus - ephemeral debug container.** When a pod *is* running but misbehaving and has no shell, you attach a debug container without restarting it:
```bash
# Works against echo-blue (which has a shell anyway, but proves the mechanism):
kubectl debug -it echo-blue --image=busybox:1.36 --target=echo -- sh
#   inside: wget -qO- localhost:8080/ ; exit
```
**Observe (B3):** `kubectl get pod echo-blue -o jsonpath='{.spec.ephemeralContainers[*].name}'` - the debug container was added to a *running* pod. Did the original `echo` container restart? (Check `RESTARTS` in `kubectl get pod echo-blue`.)

---

## Cleanup
```bash
kubectl delete namespace lab-01-fundamentals
```
No cloud LB/volume was created in this lab - deleting the namespace is enough.

---
*Now read [`lecture.md`](lecture.md) and grade your Predict predictions.*
