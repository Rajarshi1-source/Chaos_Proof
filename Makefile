# ChaosProof - build targets (§27 quick start)
# Phase 0: stubs. Each phase replaces its stub with the real recipe.

.PHONY: kind-up target-app load experiment dashboard

kind-up:        ## kind 1.36 + kube-prometheus-stack + Litmus + k6 Operator (Phase 1)
	@echo "[stub] Phase 1: kind cluster + kube-prometheus-stack (5s scrape on target) + Litmus (exact tag) + k6 Operator"
	@exit 1

target-app:     ## Build + load the three Spring Boot 4 service images (Phase 1)
	@echo "[stub] Phase 1: docker build x3, kind load, helm install charts/target-app"
	@exit 1

load:           ## k6 TestRun, 120 rps open model (Phase 2)
	@echo "[stub] Phase 2: kubectl apply profiles/steady_120rps.js as TestRun CR"
	@exit 1

experiment:     ## make experiment NAME=pod_kill_payment_svc (Phase 3)
	@echo "[stub] Phase 3: chaosctl run $(NAME)"
	@exit 1

dashboard:      ## Next.js dashboard at :3000 (Phase 6)
	@echo "[stub] Phase 6: cd dashboard && npm run dev"
	@exit 1
