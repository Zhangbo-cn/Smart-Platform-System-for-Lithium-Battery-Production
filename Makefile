# Battery Agent Platform — 常用命令

.PHONY: test test-all lint clean install

# ── 测试 ──────────────────────────────────────────────────
test:
	python -m pytest packages/harness-core/tests/ -q

test-all:
	@echo "Running all tests..."
	python -m pytest packages/harness-core/tests/ -q
	python -m pytest packages/platform-contracts/tests/ -q
	python -m pytest tests/ -q
	python -m pytest services/planner-agent/tests/ -q
	python -m pytest services/client-gateway/tests/ -q
	python -m pytest services/orchestrator/tests/ -q
	python -m pytest services/capability-registry/tests/ -q
	python -m pytest services/agent_template/tests/ -q
	python -m pytest services/a2a_server/rca-agent/tests/ -q
	python -m pytest services/a2a_server/report-agent/tests/ -q
	python -m pytest services/a2a_server/triage-agent/tests/ -q
	@echo "--- MCP tests (run individually due to path isolation) ---"
	for srv in mes_server scada_server erp_server lims_server qms_server eam_server wms_server plc_server knowledge_server; do \
		python -m pytest "services/mcp/$$srv/tests/" -q; \
	done
	@echo "Done"

test-mcp:
	@for srv in mes_server scada_server erp_server lims_server qms_server eam_server wms_server plc_server knowledge_server; do \
		python -m pytest "services/mcp/$$srv/tests/" -q || true; \
	done

# ── 安装 ──────────────────────────────────────────────────
install:
	pip install -e packages/platform-contracts
	pip install -e packages/harness-core

# ── 语法检查 ──────────────────────────────────────────────
lint:
	@echo "Checking syntax..."
	python -c "
import ast
from pathlib import Path
for f in sorted(Path('.').rglob('*.py')):
	if '.claude' in f.parts or '__pycache__' in f.parts:
		continue
	ast.parse(f.read_text(encoding='utf-8'))
" && echo "All files OK"

# ── 清理 ──────────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
	find . -type f -name "*.pyc" -delete 2>/dev/null
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null
	@echo "Cleaned"

# ── 依赖 ──────────────────────────────────────────────────
dep-graph:
	@echo "Package dependency graph:"
	@echo "  platform-contracts <- harness-core"
	@echo "  platform-contracts <- rca-agent"
	@echo "  harness-core       <- rca-agent"
	@echo "  platform-contracts <- planner-agent"
	@echo "  platform-contracts <- orchestrator"
	@echo "  platform-contracts <- client-gateway"
	@echo "  platform-contracts <- capability-registry"
