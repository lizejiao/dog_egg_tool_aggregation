"""终端报告输出：因子表 / 决策 / 执行轨迹 / 检查点视角。"""

from __future__ import annotations

from listing_guard.runner import verdict_bucket
from listing_guard.state import AuditState

W = 78


def _line(char: str = "─") -> str:
    return char * W


def print_report(result: dict) -> None:
    state: AuditState = result["state"]
    fixture = result["fixture"]
    verdict = state.get("verdict", "?")

    print(_line("━"))
    print(f"审核单号 {result['audit_id']}    检查点轨迹 {result['history_steps']} 步"
          f"    策略版本 {state.get('policy_version', '-')}")
    print(_line())
    print(f"房源：{state.get('listing', {}).get('title', '-')}")

    print(_line())
    print("① 验真因子（加权合成 · 命中清单可解释）")
    factors = state.get("factors", {})
    if factors:
        print(f"  {'因子':<14}{'类型':<8}{'权重':<6}{'得分':<7}{'状态':<8}说明")
        for f in sorted(factors.values(), key=lambda x: -x["weight"]):
            status = "否决!" if f.get("veto_triggered") else ("降级" if f.get("degraded") else ("命中" if f.get("hit") else "通过"))
            kind = "模型" if f["kind"] == "probabilistic" else "规则"
            print(f"  {f['name'][:12]:<14}{kind:<8}{f['weight']:<6}{f['score']:<7}{status:<8}{f['detail'][:34]}")
    else:
        print("  （未进入算子阶段）")

    print(_line())
    print(f"② 加权置信度：{state.get('score', '-')}"
          + (f"    否决项：{'、'.join(state['veto_hits'])}" if state.get("veto_hits") else ""))
    if state.get("injection_hits"):
        print(f"③ 注入防护：命中 {state['injection_hits']}")

    print(_line())
    bucket = verdict_bucket(verdict)
    print(f"④ 终态裁决：{verdict}（bucket: {bucket}）    fixture 期望：{fixture.get('expected', '-')}")
    match = "✓" if bucket == fixture.get("expected") else "✗ 与期望不符"
    print(f"   {match}")
    if state.get("opinion"):
        print("⑤ AI 评判意见（供人工参考，不影响路由）:")
        for line in state["opinion"].splitlines():
            print(f"   {line}")
    if state.get("human_decision"):
        print(f"⑥ 人工裁决：{state['human_decision']}")

    print(_line())
    print("⑦ 执行轨迹（events，与检查点 history 对应）")
    for ev in state.get("events", []):
        print(f"   {ev}")
    if result.get("pending"):
        print(f"   ⏸ 仍挂起于：{result['pending']}（未自动恢复）")
    print(_line("━"))
