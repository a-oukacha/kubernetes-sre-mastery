# Lab 19 - RBAC, ServiceAccounts, Pod Security & secrets · **Solution**
**Patterns: Access Control + Process Containment + Secure Configuration Source: KIA 12,13; KP "Access Control"/"Process Containment"/"Secure Configuration" Est:** 60 min

> The worked lab, with every command. Try the [exercise](lab.md) first; the checkpoint answers and the
> *why* are in [`lecture.md`](lecture.md).

## Objective
Give a workload the **least privilege it needs and prove the boundary holds: a ServiceAccount that can list pods but not delete them, a pod hardened so it can't be root or escalate, a namespace whose admission gate** rejects insecure pods outright, and a secret managed by a real tool instead of plaintext in Git. Then see what `cluster-admin` over-privilege actually buys an attacker - and remediate it.

## Concepts exercised
- ServiceAccount; the projected SA token a pod authenticates with
- Role / RoleBinding (namespaced) vs ClusterRole / ClusterRoleBinding (cluster-scoped)
- verbs (`get`/`list`/`watch`/`create`/`update`/`delete`); RBAC is deny-by-default + additive
- `kubectl auth can-i [--as=system:serviceaccount:ns:sa]`
- `securityContext`: `runAsNonRoot`, `runAsUser`, `capabilities.drop: [ALL]`, `readOnlyRootFilesystem`, `allowPrivilegeEscalation: false`, `seccompProfile: RuntimeDefault`
- Pod Security Standards (`privileged`/`baseline`/`restricted`) enforced via PSA **namespace labels**
- secret management (External Secrets Operator / Sealed Secrets) vs committed plaintext (Base64 ≠ encryption)

## Prerequisites
- Labs **01 (kubectl fluency; `describe`/`logs`/`get -o yaml`) and 04** (you know Base64 ≠ encryption).
- A reachable cluster (`kubectl get nodes` -> `Ready`). PSA is built in on Kubernetes ≥ 1.25; no add-on needed.

## Setup
```bash
# Create the namespace WITHOUT the enforce label yet, so step 4 can admit an
# insecure pod and step 6 can show the contrast when we turn enforcement on.
kubectl create namespace lab-19-rbac-podsecurity
kubectl label namespace lab-19-rbac-podsecurity \
  app.kubernetes.io/part-of=k8s-sre-course course.lab=19
kubens lab-19-rbac-podsecurity          # or add -n lab-19-rbac-podsecurity to every command
```
**Predict (0):** You have not bound any Role yet. Run `kubectl auth can-i list pods --as=system:serviceaccount:lab-19-rbac-podsecurity:pod-reader`. Yes or no - and why, given RBAC's default?

---

## Steps

### 1. Create the identity: SA + a minimal Role + RoleBinding
```bash
kubectl apply -f manifests/01-reader-rbac.yaml
kubectl get sa,role,rolebinding -l course.lab=19
```
**Observe (1):** Open `01-reader-rbac.yaml`. The Role grants exactly `get` and `list` on `pods` - no `delete`, no other resource, no cluster scope. The RoleBinding ties that Role to the `pod-reader` ServiceAccount. This is the entire grant.

### 2. Run a pod AS that ServiceAccount - it lists pods OK
```bash
kubectl apply -f manifests/02-kubectl-client.yaml
kubectl wait --for=condition=Ready pod/kubectl-client --timeout=60s
kubectl logs kubectl-client --tail=20
```
**Predict (2):** This pod runs `kubectl` from inside the cluster, authenticating with the `pod-reader` token (not your kubeconfig). Of its two looped commands - `get pods` and `delete pod kubectl-client` - which line in the logs succeeds and which prints an error?

### 3. The pod tries to delete a pod - FORBIDDEN
```bash
kubectl logs kubectl-client --tail=20 | grep -i -A1 'delete\|forbidden'
```
**Observe (3): The `get pods` block lists the namespace's pods. The `delete` block prints a `forbidden` error naming the subject (`system:serviceaccount:lab-19-rbac-podsecurity:pod-reader`) and the missing verb. The token authenticated fine - RBAC authorized** the read and **denied** the delete.

### 4. Confirm the deny from your seat with `auth can-i --as`
```bash
kubectl auth can-i list   pods --as=system:serviceaccount:lab-19-rbac-podsecurity:pod-reader   # -> yes
kubectl auth can-i delete pods --as=system:serviceaccount:lab-19-rbac-podsecurity:pod-reader   # -> no
```
**Prove it (4):** `list` -> `yes`, `delete` -> `no`. `auth can-i --as` impersonates the subject and asks the API server's authorizer directly - the same decision the pod hit in step 3, without running anything.

### 5. Grant the missing verb - now the delete is allowed
```bash
kubectl apply -f manifests/03-add-delete-verb.yaml          # Role now has get/list/delete
kubectl auth can-i delete pods --as=system:serviceaccount:lab-19-rbac-podsecurity:pod-reader   # -> yes
kubectl logs kubectl-client --tail=20 | grep -i -A1 'delete'
```
**Predict (5):** RBAC is additive and re-evaluated per request. After adding the `delete` verb, what does the kubectl-client pod's next `delete pod kubectl-client` loop do - and what happens to the pod itself? (Watch with `kubectl get pod kubectl-client -w`.)

> If the pod deletes itself, re-apply it: `kubectl apply -f manifests/02-kubectl-client.yaml`.

### 6. Harden a pod, then turn on the admission gate
First deploy the hardened pod into the still-unenforced namespace (it runs fine):
```bash
kubectl apply -f manifests/04-hardened-pod.yaml
kubectl wait --for=condition=Ready pod/hardened --timeout=60s
kubectl get pod hardened -o jsonpath='{.spec.securityContext}{"\n"}'
```
Now enforce the `restricted` Pod Security Standard via **namespace labels**:
```bash
kubectl apply -f manifests/06-namespace-psa.yaml
kubectl get ns lab-19-rbac-podsecurity -o jsonpath='{.metadata.labels}' | tr ',' '\n' | grep pod-security
```
**Observe (6): Open `04-hardened-pod.yaml` and `06-namespace-psa.yaml`. The hardened pod sets `runAsNonRoot`, `runAsUser`, drops ALL capabilities, read-only root FS, `allowPrivilegeEscalation: false`, and `seccompProfile: RuntimeDefault` - exactly the `restricted` checklist. The namespace now carries `pod-security.kubernetes.io/enforce: restricted`. The already-running hardened pod is unaffected; the gate applies to new** admissions.

### 7. Deploy a root/privileged pod - admission REJECTS it
```bash
kubectl apply -f manifests/05-privileged-pod-rejected.yaml
```
**Predict (7):** The `rootbox` pod is `privileged: true`, runs as UID 0, keeps all caps, has no seccomp. Will `apply` create it (and let the kubelet sort it out later), or will the request be refused at the API server - and what will the message name?

---

## Verify
```bash
# (a) RBAC: the deny flipped to allow only after you added the verb.
kubectl auth can-i delete pods --as=system:serviceaccount:lab-19-rbac-podsecurity:pod-reader   # -> yes

# (b) the kubectl-client pod's delete attempt printed a forbidden RBAC error
#     (re-show it from before step 5, in the older log lines if the pod was recreated):
kubectl logs kubectl-client | grep -i forbidden | head -1                # -> "...is forbidden..." (or empty if post-grant)

# (c) PSA rejects the insecure pod with a clear restricted-violation message:
kubectl apply -f manifests/05-privileged-pod-rejected.yaml 2>&1 | head -3   # -> 'violates PodSecurity "restricted...'
kubectl get pod rootbox 2>&1 | head -1                                    # -> NotFound (never stored)

# (d) the hardened pod is admitted and Running:
kubectl get pod hardened -o jsonpath='{.status.phase}{"\n"}'              # -> Running
```
yes Success = `auth can-i delete` went **no -> yes after the grant; the client pod logged a forbidden delete while the verb was absent; PSA rejects `rootbox` with a `violates PodSecurity "restricted"` message; the hardened** pod runs.

### Secrets: manage, don't commit (Base64 ≠ encryption - lab 04)
```bash
# A real Secret is only Base64-encoded in the object - anyone who can read it decodes in one step:
kubectl create secret generic demo-secret --from-literal=API_TOKEN=s3cr3t
kubectl get secret demo-secret -o jsonpath='{.data.API_TOKEN}' | base64 -d; echo   # -> s3cr3t
```
**Observe (V1): Base64 is encoding, not encryption - so plaintext (or this object) must never live in Git. Open `manifests/08-managed-secret.yaml`: it documents the two real options - External Secrets Operator (sync from a vault, auto-rotate) and Sealed Secrets** (encrypt with the controller's public key so the ciphertext is safe to commit). Apply them only if you have the operator installed; the stub explains both.

### EKS
Give the `pod-reader` SA scoped AWS access (e.g. read one S3 bucket) **without static keys**, using **IRSA**:
```bash
eksctl create iamserviceaccount \
  --cluster "$CLUSTER" --namespace lab-19-rbac-podsecurity \
  --name pod-reader --attach-policy-arn arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess \
  --approve --override-existing-serviceaccounts
# This annotates the SA with eks.amazonaws.com/role-arn=<role>. The projected
# token is exchanged via the OIDC provider for short-lived AWS creds - no keys.
kubectl get sa pod-reader -o jsonpath='{.metadata.annotations}'; echo
```
**EKS Pod Identity** is the newer alternative (an add-on + `aws eks create-pod-identity-association`, no OIDC annotation). Either way the pod gets scoped, rotating cloud creds - the cloud analogue of the least-privilege RBAC you built above. With ESO (above), the `SecretStore` authenticates to AWS Secrets Manager via this same IRSA SA.

### OVH
OVH Managed Kubernetes has **no IAM-per-pod** (no IRSA equivalent). To give a pod scoped access to an OVH object-storage bucket or external API, you use one of:
- **External Secrets Operator** against a vault (HashiCorp Vault / OVH-hosted secrets) - `08-managed-secret.yaml` Option A, with the `SecretStore` provider set to `vault` and an AppRole/token instead of IRSA.
- **Sealed Secrets** - encrypt the credential into Git, decrypt in-cluster (Option B).

State the gap explicitly in any design doc: workload identity is **not** universal; on OVH you provision a credential into a managed secret, you do not federate an IAM role onto a pod.

---

## Break it - cluster-admin over-privilege, then remediate
The classic "just give it admin to make it work." Bind the all-powerful `cluster-admin` ClusterRole to the humble `pod-reader` SA:
```bash
kubectl apply -f manifests/07-clusteradmin-binding.yaml
kubectl auth can-i '*' '*' --as=system:serviceaccount:lab-19-rbac-podsecurity:pod-reader   # -> yes
kubectl auth can-i delete pods -n kube-system \
  --as=system:serviceaccount:lab-19-rbac-podsecurity:pod-reader                            # -> yes (!)
kubectl auth can-i get secrets -A \
  --as=system:serviceaccount:lab-19-rbac-podsecurity:pod-reader                            # -> yes (!)
```
**Predict (B1):** The SA's namespaced Role still only lists/deletes pods in one namespace. After this ClusterRoleBinding, what is the blast radius if the `kubectl-client` pod (which runs as this SA) is compromised - could it delete pods in `kube-system` or read every Secret cluster-wide?

**Observe (B2):** `auth can-i '*' '*'` returns `yes`. A ClusterRoleBinding to `cluster-admin` grants every verb on every resource in **every** namespace, cluster scope included - the pod's identity is now effectively cluster owner. One leaked token = full cluster compromise.

Now **remediate** - remove the binding and confirm least privilege is restored:
```bash
kubectl delete -f manifests/07-clusteradmin-binding.yaml
kubectl auth can-i delete pods -n kube-system \
  --as=system:serviceaccount:lab-19-rbac-podsecurity:pod-reader                            # -> no
kubectl auth can-i delete pods \
  --as=system:serviceaccount:lab-19-rbac-podsecurity:pod-reader                            # -> yes (only its own ns, from step 5)
```
**Prove it (B3): After deleting the ClusterRoleBinding, the SA is back to exactly its namespaced Role: it can delete pods in its own namespace but `no` in `kube-system`. Note the escalation path the lecture warns about: a subject that can create/patch Roles or bind ClusterRoles** can grant itself anything - those verbs are privilege escalation in disguise.

---

## Cleanup
```bash
# ClusterRoleBindings are CLUSTER-SCOPED - deleting the namespace does NOT
# remove them. Delete it explicitly (harmless if Break-it already removed it):
kubectl delete -f manifests/07-clusteradmin-binding.yaml --ignore-not-found

# Everything else lives in the namespace and goes with it:
kubectl delete namespace lab-19-rbac-podsecurity
```
No cloud LB/volume was created in this lab. On EKS, if you ran the IRSA step, delete the IAM service account too: `eksctl delete iamserviceaccount --cluster "$CLUSTER" --namespace lab-19-rbac-podsecurity --name pod-reader` (the IAM role/policy attachment is cluster-external).

---
*Now read [`lecture.md`](lecture.md) and grade your Predict predictions.*
