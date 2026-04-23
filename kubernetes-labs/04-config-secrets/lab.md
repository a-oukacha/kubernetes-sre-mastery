# Lab 04 - Configuration & secrets · **Exercise**
**Patterns: EnvVar Config + Configuration Resource + Immutable Configuration + Configuration Template Source:** KIA 7; KP 19-22 **Est:** 50 min

> **This is the exercise - the commands are deliberately *not* given.** Your job is to work out the
> `kubectl` invocations yourself; that *is* the skill. Attempt every task and write down your answer to
> every **Predict / Observe / Prove it** before peeking. When you're stuck or done, [`solution.md`](solution.md) has the
> exact commands + the output you should have seen + every checkpoint answer. Then read
> [`lecture.md`](lecture.md) for the course.

## Objective
Externalize configuration out of the image and into ConfigMaps and Secrets, and internalize the one behavior that bites everyone: a ConfigMap mounted as a file updates live in the running pod; the same ConfigMap consumed as an env var is frozen until the pod restarts. Then handle secrets correctly (Base64 ≠ encryption) and ship per-environment config with Kustomize overlays.

## Concepts exercised
- ConfigMap consumed **three ways**: single env (`env.valueFrom.configMapKeyRef`), bulk env (`envFrom`), and a mounted file (volume)
- Secret consumed as a mounted file **and** as an env var
- the env-vs-file update behavior (live file sync vs frozen env)
- `immutable: true` on a ConfigMap and what it rejects
- Kustomize `base/` + `overlays/dev|prod` that patch the rendered config
- secrets are only **Base64-encoded** in the object, not encrypted

## Prerequisites
- Labs 01-02 done (kubectl fluency; you can read `describe`/`logs`/`get -o yaml`).
- A reachable cluster (2-3 `Ready` nodes).
- `KUBE_EDITOR` set for live edits (e.g. nano).

## Setup
Create a namespace **`lab-04-config-secrets` and make it the default for the rest of this lab. Then deploy the dev** environment by building the kustomize overlay under `manifests/overlays/dev` into the namespace; this brings up a ConfigMap (`app-config`), a Secret (`app-secret`), and a Deployment (`config-demo`). Wait for the rollout to finish before you start.

**Predict (0): The Deployment consumes `app-config` as env vars and as a mounted file, and `app-secret` the same two ways. Before looking - in the pod's logs, will the env `GREETING` value and the `greeting =` line inside the mounted `app.conf` show the same** value right now?

---

## Tasks

### 1. See config arrive three ways
Find the running `config-demo` pod (select it by its `app.kubernetes.io/name` label). The container runs a loop that prints its environment plus both mounted files every few seconds - read its recent log output. Then inspect, from inside the running container, the `GREETING`, `LOG_LEVEL`, and `FEATURE_FLAG` environment variables, and the contents of the mounted file at `/etc/appconfig/app.conf`.

**Observe (1): `GREETING` arrives as a single env var (via `configMapKeyRef`) and** as a line in the mounted file, while `LOG_LEVEL`/`FEATURE_FLAG` arrive via bulk `envFrom`. Confirm all three consumption styles resolve to the dev values.

### 2. Prove the env vs file difference - change the ConfigMap live
Edit the **live `app-config` ConfigMap in place and change two things at once: the `GREETING` key, and the `greeting =` line inside the embedded `app.conf` (change both to a new sentinel value, e.g. `hello from EDITED`). Do not** restart or re-deploy the pod.

Now, against the same already-running pod, repeatedly re-read the mounted `/etc/appconfig/app.conf` over roughly a minute or more (the kubelet's mount sync takes tens of seconds), and separately check the `GREETING` environment variable.

**Predict (2): Which of the two will eventually show the new value in the already-running** pod - the mounted file, the env var, both, or neither?

**Prove it (2):** Establish, with your own observations, exactly which of the mounted file and the env var picks up the edit and which does not, while the pod keeps running untouched.

### 3. Make the env var catch up (and confirm only a restart does it)
Trigger a rolling restart of the `config-demo` Deployment and wait for it to complete. Re-find the (now new) pod and re-inspect its `GREETING` environment variable.

**Observe (3):** Determine what it takes for the env var to reflect your ConfigMap edit, and explain at what moment an env var's value is fixed for the life of a container.

### 4. Immutable ConfigMap - create it, then try to change it
Apply the manifest at `manifests/extras/configmap-immutable.yaml` (it defines `app-config-v1`) and confirm its `immutable` field. Then attempt to change one of its `data` keys via a patch.

**Predict (4):** Will the change succeed, or be rejected? If rejected, what does the API server tell you you'd have to do instead?

**Prove it (4):** Carry out the attempted edit and capture the API server's response. From it, state the only supported path forward for changing an immutable ConfigMap, and name the pattern that path embodies.

### 5. Per-environment config with Kustomize overlays
Without applying anything, render the `dev` overlay and the `prod` overlay (both under `manifests/overlays/`) and compare the resulting config values (`GREETING` / `LOG_LEVEL` and the `greeting` / `log_level` lines in `app.conf`). Diff the two rendered outputs side by side.

**Observe (5):** Confirm that one shared base yields two different rendered configs, identify which values each environment sets, and verify that the base manifests themselves are never edited - the overlay patches them.

---

## Verify
Demonstrate success with observable signals: (a) the mounted `app.conf` in the running pod reflects the edit you made in task 2; (b) the `GREETING` env var matches only after the task-3 restart; (c) the immutable ConfigMap rejects an attempted data edit; and (d) the `dev` and `prod` overlays render different config.

yes Success = mounted file updated live, env updated only after restart, immutable edit rejected, dev/prod builds differ.

---

## Break it - a "secret" that isn't secret
Apply `manifests/extras/configmap-leaky.yaml`, which stuffs a secret value (`DB_PASSWORD`) into a plain ConfigMap - the classic mistake. Then inspect, as object data, what anyone with read access to that ConfigMap would see.

**Predict (B1):** Is `DB_PASSWORD` stored encrypted, hashed, or in clear text in that ConfigMap?

Now turn to the *real* `app-secret` Secret you deployed. Look at how its data appears in the object itself, recover the underlying `API_TOKEN` value from that stored form by hand, and look at how the secret-backed env var / references show up in the pod's `describe` output.

**Observe (B2):** Establish how little effort it takes for anyone who can read the Secret to recover the plaintext, and state plainly what the stored form is and is not. Then say why a secret living in a ConfigMap is even worse, and why neither belongs in source control as-is.

**Prove it (B3):** The Secret-as-**mounted-file** alternative is already wired up. Read the mounted secret file at `/etc/appsecret/db-password` from inside the pod, and show that this delivery path keeps the value out of the env-var listing in `kubectl describe pod`. (Encryption-at-rest in etcd is a separate cluster feature - see the cloud notes.)

### EKS
No cloud-specific steps to run. Note for the lecture: etcd **encryption-at-rest** for Secrets is configured via a KMS key (EKS "Secrets encryption" / `EncryptionConfig`); without it, Secrets sit Base64-only in etcd.

### OVH
No cloud-specific steps to run. OVH Managed Kubernetes encrypts the managed etcd at rest on the platform side; you still treat Base64 as non-secret and never commit plaintext.

---

## Cleanup
Delete the `lab-04-config-secrets` namespace. No cloud LB/volume was created here, so that's enough. (The immutable/leaky ConfigMaps live inside the namespace, so they go too.)

---
*Stuck or finished? -> [`solution.md`](solution.md) for the worked commands & answers, then [`lecture.md`](lecture.md) for the course.*
