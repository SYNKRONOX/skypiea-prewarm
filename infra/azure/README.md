# Azure cloud run

Owner: Ch. Dhanunjaya

The cloud run uses one Azure for Students VM running [k3s](https://k3s.io/), with the same
manifests as the local kind cluster (`deploy/k8s/`).

1. **Set a budget alert first** (Cost Management → Budgets) before creating anything.
2. Create one Linux VM and install k3s: `curl -sfL https://get.k3s.io | sh -`.
3. Push the tool, shaper and controller images to a cloud registry (e.g. Azure Container
   Registry or GHCR) and point the manifests at it.
4. Copy the kubeconfig to your machine. Never commit it (it is in `.gitignore`).
5. `kubectl apply -f deploy/k8s/` and run the experiments.
6. **Delete the VM after each run** so the credit is not drained while idle.
