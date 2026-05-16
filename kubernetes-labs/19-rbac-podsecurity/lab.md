# Lab 19 - RBAC, ServiceAccounts, Pod Security & secrets · **Exercise**
**Patterns: Access Control + Process Containment + Secure Configuration Source: KIA 12,13; KP "Access Control"/"Process Containment"/"Secure Configuration" Est:** 60 min

> **This is the exercise - the commands are deliberately *not* given.** Your job is to work out the
> `kubectl` invocations yourself; that *is* the skill. Attempt every task and write down your answer to
> every **Predict / Observe / Prove it / Break it** before peeking. When you're stuck or done, [`solution.md`](solution.md) has the
> exact commands + the output you should have seen + every checkpoint answer. Then read
> [`lecture.md`](lecture.md) for the course.

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
Create the namespace **`lab-19-rbac-podsecurity`** and make it your default, but **do not** apply the PSA enforce label yet - later tasks need the namespace to admit an insecure pod first, then show the contrast once enforcement is on. Label the namespace with `app.kubernetes.io/part-of=k8s-sre-course` and `course.lab=19`.

**Predict (0):** You have not bound any Role yet. Ask the authorizer whether the (not-yet-bound) `pod-reader` ServiceAccount can `list pods`. Yes or no - and why, given RBAC's default?

---

## Tasks

### 1. Create the identity: SA + a minimal Role + RoleBinding
Apply `manifests/01-reader-rbac.yaml`, then list the ServiceAccount, Role and RoleBinding you just created (filter by the `course.lab=19` label).

**Observe (1):** Open `01-reader-rbac.yaml`. Confirm the Role grants exactly two verbs on a single resource, the RoleBinding ties that Role to the `pod-reader` ServiceAccount, and nothing reaches beyond the namespace. Name the verbs and the resource - and what is deliberately *absent* from the grant.

### 2. Run a pod AS that ServiceAccount - it lists pods OK
Apply `manifests/02-kubectl-client.yaml`, wait for it Ready, and read its recent logs. This pod runs `kubectl` from inside the cluster, authenticating with the `pod-reader` token (not your kubeconfig); it loops two commands - a `get pods` and a `delete pod kubectl-client`.

**Predict (2):** Of its two looped commands, which line in the logs succeeds and which prints an error?

### 3. The pod tries to delete a pod - FORBIDDEN
From the same pod's logs, isolate the lines around the delete attempt and the forbidden error.

**Observe (3):** Confirm the `get pods` block lists the namespace's pods while the `delete` block is refused. The token authenticated fine - so which step authorized the read and which denied the delete? Identify the subject string and the missing verb named in the error.

### 4. Confirm the deny from your seat with `auth can-i --as`
Without running anything in a pod, impersonate the `pod-reader` ServiceAccount from your own kubectl and ask the API server's authorizer directly whether that subject can `list` pods and whether it can `delete` pods.

**Prove it (4):** `list` -> `yes`, `delete` -> `no`. Convince yourself this is the *same* authorization decision the pod hit in task 3, just queried directly.

### 5. Grant the missing verb - now the delete is allowed
Apply `manifests/03-add-delete-verb.yaml` (the Role now also carries `delete`), then re-ask the authorizer whether the SA can delete pods, and re-check the client pod's delete loop in its logs.

**Predict (5):** RBAC is additive and re-evaluated per request. After adding the `delete` verb, what does the kubectl-client pod's next `delete pod kubectl-client` loop do - and what happens to the pod itself?

> If the pod deletes itself, re-apply `manifests/02-kubectl-client.yaml`.

### 6. Harden a pod, then turn on the admission gate
First deploy the hardened pod from `manifests/04-hardened-pod.yaml` into the still-unenforced namespace, wait for it Ready, and inspect its `spec.securityContext`. Then turn on enforcement of the `restricted` Pod Security Standard by applying the namespace labels in `manifests/06-namespace-psa.yaml`, and read back the namespace's `pod-security.*` labels.

**Observe (6): Open `04-hardened-pod.yaml` and `06-namespace-psa.yaml`. Confirm the hardened pod satisfies the full `restricted` checklist (`runAsNonRoot`, `runAsUser`, drop ALL capabilities, read-only root FS, `allowPrivilegeEscalation: false`, `seccompProfile: RuntimeDefault`) and that the namespace now carries the `enforce: restricted` label. Does turning on enforcement evict the already-running hardened pod, or does the gate only apply to new** admissions?

### 7. Deploy a root/privileged pod - admission REJECTS it
Apply `manifests/05-privileged-pod-rejected.yaml`. The `rootbox` pod is `privileged: true`, runs as UID 0, keeps all capabilities, and has no seccomp profile.

**Predict (7):** Will `apply` create it (and let the kubelet sort it out later), or will the request be refused at the API server - and what will the rejection message name?

---

## Verify
Demonstrate success with observable signals:
- **(a)** The authorizer's `delete pods` answer for the `pod-reader` SA is now `yes` - but was `no` before you added the verb.
- **(b)** The kubectl-client pod logged a `forbidden` RBAC error on its delete attempt while the verb was still absent.
- **(c)** PSA rejects the insecure `rootbox` pod with a clear `restricted`-violation message, and `rootbox` was never stored (a `get` returns NotFound).
- **(d)** The hardened pod is admitted and its `status.phase` reads `Running`.

yes Success = `auth can-i delete` went **no -> yes after the grant; the client pod logged a forbidden delete while the verb was absent; PSA rejects `rootbox` with a `violates PodSecurity "restricted"` message; the hardened** pod runs.

### Secrets: manage, don't commit (Base64 ≠ encryption - lab 04)
Create a generic Secret with a literal token, then read the value back out of the object and decode it - proving in one step that a "Secret" is only Base64-encoded at rest, not encrypted.

**Observe (V1): Since Base64 is encoding and not encryption, the plaintext (or this object) must never live in Git. Open `manifests/08-managed-secret.yaml`: it documents the two real options - External Secrets Operator (sync from a vault, auto-rotate) and Sealed Secrets** (encrypt with the controller's public key so the ciphertext is safe to commit). Apply them only if you have the operator installed; the stub explains both. State which option fits a team that wants secrets *in* Git and which fits a team with a central vault.

### EKS
Describe how you would give the `pod-reader` SA scoped AWS access (e.g. read one S3 bucket) **without static keys**, using **IRSA: which SA annotation carries the IAM role, and how the projected token becomes short-lived AWS creds via the cluster's OIDC provider. Note that EKS Pod Identity** is the newer alternative (an add-on plus a pod-identity association, no OIDC annotation). Either way the pod gets scoped, rotating cloud creds - the cloud analogue of the least-privilege RBAC you built above. State how this same SA would let an ESO `SecretStore` authenticate to AWS Secrets Manager.

### OVH
Explain why OVH Managed Kubernetes has **no IAM-per-pod** (no IRSA equivalent), and which two mechanisms you would use instead to give a pod scoped access to an OVH object-storage bucket or external API:
- **External Secrets Operator** against a vault (HashiCorp Vault / OVH-hosted secrets) - `08-managed-secret.yaml` Option A, with the `SecretStore` provider set to `vault` and an AppRole/token instead of IRSA.
- **Sealed Secrets** - encrypt the credential into Git, decrypt in-cluster (Option B).

State the gap explicitly in any design doc: workload identity is **not** universal; on OVH you provision a credential into a managed secret, you do not federate an IAM role onto a pod.

---

## Break it - cluster-admin over-privilege, then remediate
The classic "just give it admin to make it work." Apply `manifests/07-clusteradmin-binding.yaml` to bind the all-powerful `cluster-admin` ClusterRole to the humble `pod-reader` SA, then impersonate the SA and ask the authorizer what it can now do: every verb on every resource (`'*' '*'`), deleting pods in `kube-system`, and reading Secrets across all namespaces.

**Predict (B1):** The SA's namespaced Role still only lists/deletes pods in one namespace. After this ClusterRoleBinding, what is the blast radius if the `kubectl-client` pod (which runs as this SA) is compromised - could it delete pods in `kube-system` or read every Secret cluster-wide?

**Observe (B2):** Confirm `auth can-i '*' '*'` returns `yes`. What scope does a ClusterRoleBinding to `cluster-admin` actually grant, and what does one leaked token from this pod now mean for the whole cluster?

Now **remediate**: delete the ClusterRoleBinding (from `manifests/07-clusteradmin-binding.yaml`), then re-query the authorizer to confirm the SA can no longer delete pods in `kube-system` but can still delete pods in its own namespace (the grant from task 5).

**Prove it (B3):** After deleting the ClusterRoleBinding, the SA is back to exactly its namespaced Role: it can delete pods in its own namespace but `no` in `kube-system`. Name the escalation path the lecture warns about - which verbs on which objects let a subject grant itself anything (privilege escalation in disguise)?

---

## Cleanup
ClusterRoleBindings are **cluster-scoped** - deleting the namespace does **not** remove them. Explicitly delete the `cluster-admin` ClusterRoleBinding you created in Break-it (harmless if you already removed it there), then delete the `lab-19-rbac-podsecurity` namespace to clean up everything else. No cloud LB/volume was created in this lab. On EKS, if you ran the IRSA step, also delete the IAM service account (the IAM role/policy attachment is cluster-external).

---
*Stuck or finished? -> [`solution.md`](solution.md) for the worked commands & answers, then [`lecture.md`](lecture.md) for the course.*
