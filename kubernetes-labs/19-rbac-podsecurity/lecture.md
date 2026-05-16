# Lecture 19 - RBAC, ServiceAccounts, Pod Security & secrets

## Answers to the lab checkpoints
- **(0)** **No.** RBAC is **deny-by-default**: a subject with no Role/RoleBinding can do nothing. The SA exists, but until a binding grants a verb, every authorization decision is `no`. There is no implicit "can at least read its own namespace" - zero means zero.
- **(2)** `get pods` **succeeds**, `delete pod` **fails**. The pod authenticated as `pod-reader` using its projected token; the Role grants `get`/`list` on pods (read works) but not `delete` (refused). Same identity, two different authorization outcomes - which is the whole lesson: authentication (who) and authorization (allowed to do what) are separate gates.
- **(3)** The error reads roughly `pods "kubectl-client" is forbidden: User "system:serviceaccount:lab-19-rbac-podsecurity:pod-reader" cannot delete resource "pods" in API group "" in the namespace "lab-19-rbac-podsecurity"`. The token was valid; RBAC denied the verb. "Forbidden" (403) means *authenticated but not authorized*, distinct from "Unauthorized" (401, *who are you?*).
- **(4)** `list` -> `yes`, `delete` -> `no`. `auth can-i --as` impersonates the subject and queries the authorizer with no side effect - your single best RBAC debugging tool. (Impersonation itself is a privileged verb; you can do it because your kubeconfig is admin.)
- **(5) The next loop's `delete pod kubectl-client` now succeeds** and the pod **deletes itself** (it had `get/list/delete` and ran the delete against its own name). RBAC is re-evaluated per request from live policy - no restart, no token refresh needed. Re-apply the pod to continue. This is the additive model: you widened capability by adding one verb, not by escalating to admin.
- **(6) The hardened pod keeps running - PSA evaluates pods at admission** (create/update), not continuously, so labeling the namespace afterward doesn't evict already-admitted pods. It does gate everything created next. (Use `audit`/`warn` first on an existing namespace to find pods that *would* fail before you flip `enforce`.)
- **(7)** The `apply` is **rejected at the API server with `Error from server (Forbidden): ... violates PodSecurity "restricted:latest"` and a list of the failing fields (`privileged != nil`, `allowPrivilegeEscalation != false`, `unrestricted capabilities`, `runAsNonRoot != true`, `seccompProfile ... not set`). The object is never stored** - `kubectl get pod rootbox` is `NotFound`. Admission runs before etcd; nothing reaches the kubelet.
- **(B1)** Blast radius = **the entire cluster**. A ClusterRoleBinding ignores the SA's namespaced Role; `cluster-admin` is `*/*/*` across all namespaces. A compromise of the kubectl-client pod could now delete `kube-system` pods, read every Secret, and rewrite RBAC. The original Role is irrelevant once a broader binding exists - RBAC is the **union** of all bindings for a subject.
- **(B2)** `auth can-i '*' '*'` -> `yes`. That is total authority. The lesson: bindings are additive and the most permissive one wins, so a single over-broad binding silently erases all your careful least-privilege work.
- **(B3)** After deleting the ClusterRoleBinding, `delete pods` in `kube-system` -> `no`, but in its own namespace -> `yes` (the namespaced Role from step 5 survives). Least privilege is restored. The escalation footnote matters: anyone who can `create`/`patch` Roles/RoleBindings - or `bind`/`escalate` - can hand themselves any permission, so guard *those* verbs as tightly as `delete secrets`.

---

## What just happened (under the hood)
Every request to the API server runs the same three-stage gauntlet, in order, before anything touches etcd:

1. Authentication (authn) - who are you? The request carries a credential: your client cert / token (kubeconfig), or, for a pod, a **projected ServiceAccount token** mounted at `/var/run/secrets/kubernetes.io/serviceaccount/token`. That token is a short-lived, audience-bound JWT the API server validates and resolves to the identity `system:serviceaccount:<ns>:<sa>`. The kubectl-client pod never had a kubeconfig - kubectl found the projected token automatically. Authn answers *who*, nothing more.

2. Authorization (authz) - are you allowed to do this? With identity established, the **RBAC authorizer asks: is there any (Role/ClusterRole) granting this (subject, verb, resource, namespace)? RBAC is deny-by-default**, **allow-only** (no deny rules), and **additive** (a subject's effective permissions are the union over all its bindings). A Role + RoleBinding scopes the grant to one namespace; a ClusterRole + ClusterRoleBinding scopes it cluster-wide. This is why `get pods` passed and `delete pods` failed in the same breath - different (verb, resource) tuples, different answers - and why one `cluster-admin` ClusterRoleBinding in Break-it overrode the tidy namespaced Role.

3. Admission - is the object itself acceptable? Past authz, **mutating admission webhooks run first (they can default/inject - this is where a service mesh injects sidecars, where IRSA tokens get projected), then validating admission runs, which includes the built-in Pod Security Admission** controller. PSA reads the namespace's `pod-security.kubernetes.io/*` labels and checks the pod spec against the chosen **Pod Security Standard**. `rootbox` cleared authn and authz fine - it died here, at validating admission, because its spec violated `restricted`. Only after all three gates does the validated object persist to etcd as desired state, and the usual controllers/scheduler/kubelet loop (lab 01) takes over.

Two ideas to carry forward:

- Least privilege is a design, not a setting. You grant the *minimum* verbs on the *minimum* resources in the *minimum* scope. The default ServiceAccount, and `cluster-admin`, are the two extremes you avoid. A workload's SA *is* its blast radius if the pod is compromised.
- The three Pod Security Standards are a ladder. `privileged` = no restrictions (system/infra pods only). `baseline` = block the known-dangerous escalations (host namespaces, privileged, hostPath, added caps) while staying broadly compatible. `restricted` = the hardened target you used: non-root, all caps dropped, seccomp `RuntimeDefault`, no privilege escalation, read-only-root-friendly. PSA enforces these purely from namespace labels - no CRD, no operator.

And the secret thread from lab 04 closes here: **Base64 is encoding, not encryption. A Secret object is one `base64 -d` away from plaintext to anyone who can read it. Real protection = (a) etcd encryption-at-rest so the stored value isn't plaintext on disk, plus (b) a secret manager (External Secrets Operator syncing a vault, or Sealed Secrets encrypting into Git) so the plaintext never lives in source control, plus (c) tight RBAC on `secrets`** so few subjects can read them.

## Dev notes
- Design the app to need nothing privileged. `runAsNonRoot: true` + an explicit non-root `runAsUser`, `capabilities.drop: ["ALL"]`, `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`, `seccompProfile: RuntimeDefault`. If the app "needs" root, that's almost always a fixable image/port choice (bind ≥1024; write to a mounted `emptyDir`, not `/`). The hardened pod shows the full set - copy it as your pod template.
- Read-only root FS forces honesty about writes. The hardened pod mounts an `emptyDir` on `/tmp` because the root FS is immutable. Knowing exactly where your app writes is good hygiene anyway.
- Don't auto-mount a token you don't use. If a pod never calls the Kubernetes API, set `automountServiceAccountToken: false` (the hardened pod does). A mounted token is a credential waiting to leak.
- Never put secrets in env vars or commit them. Env-injected secrets show up in `kubectl describe pod`, crash dumps, and child-process environments. Prefer mounted Secret files, and source the value from a manager (lab 04 + this lab).

## DevOps / Platform notes
- PSA is the floor; policy engines are the ceiling. Pod Security Admission only covers pod-level security fields and only three fixed levels. For org policy ("every image from our registry", "every pod has a cost label", "no `:latest`") use **Kyverno** or **OPA Gatekeeper** as validating webhooks. Run them in `audit` before `enforce`, same as PSA.
- Roll out `restricted` with `warn`/`audit` first. Label existing namespaces `warn: restricted` + `audit: restricted`, watch the audit log / kubectl warnings to find offenders, fix them, *then* set `enforce`. Flipping `enforce` cold breaks deploys (and surfaces in CI as failed `kubectl apply`).
- **Turn on etcd encryption-at-rest.** Without it, Secrets sit Base64-only in etcd; a snapshot/backup leak = plaintext. On EKS this is the "Secrets encryption" KMS option / `EncryptionConfig`; OVH encrypts managed etcd platform-side (still treat Base64 as non-secret).
- Kill the default SA token auto-mount where unused, rotate secrets on a schedule (ESO `refreshInterval` does this for you), and audit ClusterRoleBindings periodically - they are cluster-scoped and easy to forget (you deleted yours explicitly in Cleanup for exactly that reason).

## Architect notes (trade-offs)
- Per-workload ServiceAccounts = blast-radius isolation. Give every workload its own SA with its own minimal Role. A compromised pod then holds only *its* SA's rights, not a shared admin token. The opposite - everything on the `default` SA, or a few god-mode SAs - means one breach is total.
- **Workload identity vs static keys.** On EKS, **IRSA / Pod Identity federates an IAM role onto an SA: the pod gets short-lived, rotating, scoped cloud creds and you never store an access key. On OVH there is no IAM-per-pod**, so you provision the credential into a managed secret (ESO from a vault, or Sealed Secrets). Architecturally these are the same goal - scoped, rotatable, non-static credentials - reached by different means; don't assume the EKS pattern ports.
- Secret rotation is a first-class design concern, not an afterthought. A manager that rotates (ESO re-pulling, KMS-backed) beats a one-time `kubectl create secret` you'll forget to rotate.

## SRE notes (failure modes, SLOs, toil)
- **Audit logs answer "who did what."** Enable API server audit logging; it's how you reconstruct an incident (which subject deleted the StatefulSet, which token read the Secrets). No audit log = no forensics.
- Periodic least-privilege review is real toil - automate it. Tools like `rbac-tool`/`kubectl-who-can` enumerate who can do dangerous verbs. The escalation paths to watch: any subject that can `create`/`patch` Roles/RoleBindings, `bind`/`escalate`, or create pods with arbitrary SAs can self-promote. Treat those verbs like `delete secrets`.
- A leaked over-privileged SA token = cluster compromise. That's why Break-it is scary: one `cluster-admin` binding on a pod's SA turns a single pod RCE into total cluster ownership. Short-lived projected tokens (the default now) shrink the window; static long-lived tokens widen it.
- PSA rejections surface in your deploy pipeline. When you flip a namespace to `enforce: restricted`, non-compliant Deployments fail to roll out - the ReplicaSet can't create pods, and you'll see `FailedCreate ... violates PodSecurity` events, not a clean error. Catch it in CI by dry-running against the target policy.

## AI/ML notes (LLM/ML serving mapping - conceptual)
- Scope cloud access per inference ServiceAccount. A model server should reach **only its own** weights bucket / model registry / vector DB - not every bucket. On EKS that's IRSA on the inference SA (read-only on `s3://models/llama-3-8b/*`); on OVH it's ESO/Sealed Secrets delivering a scoped object-storage credential. A compromised inference pod then can't exfiltrate every model.
- Protect model weights and provider API keys as managed secrets, never in env vars or Git. An OpenAI/Anthropic API key or proprietary weights leaked from a pod's environment is a direct financial/IP loss. This ties back to lab 04 (Secret handling) and lab 08 (egress NetworkPolicy so even a leaked key can't phone home).
- Multi-tenant model platforms isolate tenants by namespace + SA + RBAC + NetworkPolicy. Each tenant gets a namespace (PSA `restricted` enforced), its own SA scoped to its models, RBAC so it can't read another tenant's Secrets, and NetworkPolicy so its pods can't reach another tenant's services. The same primitives you used here, composed.

## Pitfalls
- The default SA's auto-mounted token. Every pod gets the `default` SA and (historically) its token mounted, usable to talk to the API. Disable auto-mount where the pod doesn't need it.
- **`cluster-admin` sprawl** - "just give it admin to make it work." It works, and it's a backdoor. Grant the verb, not the role.
- Secrets in env vars or committed to Git. They leak via `describe`, dumps, and history. Base64 ≠ encryption; use a manager + etcd-at-rest encryption.
- **PSA set to `warn` only.** A warning blocks nobody. If you mean it, `enforce`. (Keep `warn`/`audit` too, for the message.)
- ClusterRoleBinding where a RoleBinding would do. Cluster scope when you meant one namespace is silent over-grant - and cluster-scoped objects survive a namespace delete (the explicit Cleanup line exists for this).

## Further reading
- **KP Part V - Security:** "Access Control" (RBAC, ServiceAccounts), "Process Containment" (securityContext, capabilities, seccomp), "Secure Configuration" (secrets, PSA).
- **KIA ch12** - Securing the API server: authentication, ServiceAccounts, RBAC (Roles, ClusterRoles, bindings).
- **KIA ch13** - Securing the cluster nodes and network: `securityContext`, PodSecurityPolicy (PSA's deprecated predecessor - note the evolution), the privilege/capability model.
- Kubernetes docs: **Pod Security Standards** and **Pod Security Admission (the namespace-label model you used); Using RBAC Authorization**; **Managing Service Accounts** and **projected token volumes**.
