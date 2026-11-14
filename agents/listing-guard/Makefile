.PHONY: demo demo-injection demo-timeout resume eval test serve clean

demo:            ## 单条全流程（挂起→回调→resume 完整叙事）
	uv run python scripts/run_case.py --case normal_01

demo-injection:  ## 注入防护：强制转人工
	uv run python scripts/run_case.py --case prompt_injection_05

demo-timeout:    ## 算子降级：政府核验超时不误杀
	uv run python scripts/run_case.py --case gov_timeout_09

resume:          ## 跨进程断点续跑（两阶段）
	uv run python scripts/resume_case.py --case normal_01
	uv run python scripts/resume_case.py --case normal_01 --resume

eval:            ## 批量评测：混淆矩阵 + 指标 + P95
	uv run python scripts/batch_eval.py

serve:           ## Web 审核控制台（http://127.0.0.1:8300）
	./scripts/serve.sh

test:            ## 单测（含 API 集成）
	uv run pytest

clean:           ## 清运行期产物
	rm -f data/checkpoints.sqlite*
	rm -f data/gov_pending.json data/metrics.jsonl
	rm -rf data/feedback .pytest_tmp
	mkdir -p data/feedback && touch data/feedback/.gitkeep
