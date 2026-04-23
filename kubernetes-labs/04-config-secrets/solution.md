# Lab 04 - Configuration & secrets · **Solution**
**Patterns: EnvVar Config + Configuration Resource + Immutable Configuration + Configuration Template Source:** KIA 7; KP 19-22 **Est:** 50 min

> The worked lab, with every command. Try the [exercise](lab.md) first; the checkpoint answers and the
> *why* are in [`lecture.md`](lecture.md).

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
- A reachable cluster (`kubectl get nodes` -> `Ready`).
- `KUBE_EDITOR` set for `kubectl edit` (e.g. `export KUBE_EDITOR=nano`).

## Setup
```bash
kubectl create namespace lab-04-config-secrets
kubens lab-04-config-secrets          # or add -n lab-04-config-secrets to every command

# Render the base + dev overlay into the namespace (ConfigMap + Secret + Deployment):
kubectl apply -k manifests/overlays/dev
kubectl rollout status deploy/config-demo
```
**Predict (0): The Deployment consumes `app-config` as env vars and as a mounted file, and `app-secret` the same two ways. Before looking - in `kubectl logs`, will the `ENV GREETING=` line and the `greeting =` line inside the mounted `app.conf` show the same** value right now?

---

## Steps

### 1. See config arrive three ways
```bash
POD=$(kubectl get pod -l app.kubernetes.io/name=config-demo -o jsonpath='{.items[0].metadata.name}')
kubectl logs "$POD" --tail=12          # the loop prints env + both mounted files every 5s
kubectl exec "$POD" -- printenv GREETING LOG_LEVEL FEATURE_FLAG
kubectl exec "$POD" -- cat /etc/appconfig/app.conf
```
**Observe (1): `GREETING` exists as an env var (from `configMapKeyRef`) and** as a line in the mounted file. `LOG_LEVEL`/`FEATURE_FLAG` arrived via `envFrom` (bulk import). Confirm all three consumption styles resolve to the dev values.

### 2. Prove the env vs file difference - change the ConfigMap live
Edit the live ConfigMap and change **both** the `GREETING` key and the `greeting` line inside `app.conf`:
```bash
kubectl edit configmap app-config
#   change GREETING:        "hello from DEV"  -> "hello from EDITED"
#   change the app.conf line greeting = ...   -> greeting = hello from EDITED
```
Now watch the running pod **without restarting it**:
```bash
# Mounted FILE - re-read it a few times over ~70s (kubelet sync is tens of seconds):
for i in 1 2 3 4 5 6 7; do kubectl exec "$POD" -- cat /etc/appconfig/app.conf | grep greeting; sleep 12; done
# ENV var - check it at the same time:
kubectl exec "$POD" -- printenv GREETING
```
**Predict (2): Which of the two will eventually show `hello from EDITED` in the already-running** pod - the mounted file, the env var, both, or neither?

**Prove it (2): The mounted `app.conf` flips to `hello from EDITED` after the kubelet sync; `printenv GREETING` still prints the old value**. Confirm both halves of that claim with the commands above.

### 3. Make the env var catch up (and confirm only a restart does it)
```bash
kubectl rollout restart deploy/config-demo
kubectl rollout status deploy/config-demo
POD=$(kubectl get pod -l app.kubernetes.io/name=config-demo -o jsonpath='{.items[0].metadata.name}')
kubectl exec "$POD" -- printenv GREETING
```
**Observe (3):** Only after the new pod starts does `printenv GREETING` show `hello from EDITED`. The env var was frozen at the *previous* container's start.

### 4. Immutable ConfigMap - create it, then try to change it
```bash
kubectl apply -f manifests/extras/configmap-immutable.yaml
kubectl get configmap app-config-v1 -o jsonpath='{.immutable}'; echo
```
Now try to edit its data:
```bash
kubectl patch configmap app-config-v1 --type merge -p '{"data":{"GREETING":"changed"}}'
```
**Predict (4):** Will the patch succeed, or be rejected? If rejected, what does the API server say you'd have to do instead?

**Prove it (4):** The patch is rejected with a message that `data` (and `immutable`) of an immutable ConfigMap cannot be changed. The only path forward is to create a *new* ConfigMap and re-point references - that's the Immutable Configuration pattern.

### 5. Per-environment config with Kustomize overlays
Render (don't apply) each overlay and diff the config that comes out:
```bash
kubectl kustomize manifests/overlays/dev  | grep -E 'GREETING|LOG_LEVEL|greeting|log_level'
kubectl kustomize manifests/overlays/prod | grep -E 'GREETING|LOG_LEVEL|greeting|log_level'
# Side-by-side:
diff <(kubectl kustomize manifests/overlays/dev) <(kubectl kustomize manifests/overlays/prod)
```
**Observe (5):** Same base, two outputs: dev renders `hello from DEV` / `log_level = debug`, prod renders `hello from PROD` / `log_level = warn`. The base manifests are never edited - the overlay patches them.

---

## Verify
```bash
POD=$(kubectl get pod -l app.kubernetes.io/name=config-demo -o jsonpath='{.items[0].metadata.name}')

# (a) mounted file reflects the edit you made in step 2:
kubectl exec "$POD" -- grep greeting /etc/appconfig/app.conf      # -> hello from EDITED

# (b) env var matches only after the step-3 restart:
kubectl exec "$POD" -- printenv GREETING                          # -> hello from EDITED

# (c) the immutable ConfigMap rejects edits:
kubectl patch configmap app-config-v1 --type merge -p '{"data":{"GREETING":"x"}}' 2>&1 | head -1   # -> error

# (d) the two overlays render different config:
diff <(kubectl kustomize manifests/overlays/dev) <(kubectl kustomize manifests/overlays/prod) >/dev/null; echo "differ? exit=$?"   # exit=1 means they differ
```
yes Success = mounted file updated live, env updated only after restart, immutable edit rejected, dev/prod builds differ.

---

## Break it - a "secret" that isn't secret
Stuff a secret value into a plain ConfigMap (the classic mistake), then look at what anyone with read access sees:
```bash
kubectl apply -f manifests/extras/configmap-leaky.yaml
kubectl get configmap leaky-config -o yaml | grep -A2 'data:'
```
**Predict (B1):** Is `DB_PASSWORD` stored encrypted, hashed, or in clear text in that ConfigMap?

Now look at the *real* Secret you deployed - both how it appears in the object and how it appears in a pod:
```bash
kubectl get secret app-secret -o yaml | grep -A3 'data:'      # the "encoded" form
kubectl get secret app-secret -o jsonpath='{.data.API_TOKEN}' | base64 -d; echo   # decode it yourself
kubectl describe pod "$POD" | grep -i -A2 -E 'API_TOKEN|app-secret'  # how the env var/refs show up
```
**Observe (B2): Anyone who can `get secret` can `base64 -d` the value in one step - Base64 is encoding, not encryption**. A secret in a ConfigMap is even worse (printed in clear text, no encoding hint). Neither belongs in source control as-is.

**Prove it (B3):** The Secret-as-**mounted-file** alternative is already wired up - show it reads cleanly without ever putting the value in an env var or `describe` output:
```bash
kubectl exec "$POD" -- cat /etc/appsecret/db-password; echo
```
Compare: the mounted file delivers the secret to the app without it showing up in `kubectl describe pod` env listings. (Encryption-at-rest in etcd is a separate cluster feature - see the cloud notes.)

### EKS
No cloud-specific steps to run. Note for the lecture: etcd **encryption-at-rest** for Secrets is configured via a KMS key (EKS "Secrets encryption" / `EncryptionConfig`); without it, Secrets sit Base64-only in etcd.

### OVH
No cloud-specific steps to run. OVH Managed Kubernetes encrypts the managed etcd at rest on the platform side; you still treat Base64 as non-secret and never commit plaintext.

---

## Cleanup
```bash
kubectl delete namespace lab-04-config-secrets
```
No cloud LB/volume was created in this lab - deleting the namespace is enough. (The immutable/leaky ConfigMaps were created with `-f` but live inside the namespace, so they go too.)

---
*Now read [`lecture.md`](lecture.md) and grade your Predict predictions.*
