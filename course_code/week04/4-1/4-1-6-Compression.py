"""python3 context_compression_demo.py
真实执行边界检查；启动行是用于模拟长日志的教学填充。
完整结果持久保存在 artifacts/test_result.json，不调用模型。
"""
from pathlib import Path
import hashlib
import json


def is_session_valid(now, expires_at):
    return now <= expires_at  # 故意保留边界错误；改成 < 可观察结果变化。


def run_tests(output_ref):
    cases = [
        ("before_expiry", 99, 100, True),
        ("at_expiry", 100, 100, False),
        ("after_expiry", 101, 100, False),
    ]
    failures, errors = [], []
    for name, now, expires_at, expected in cases:
        try:
            actual = is_session_valid(now, expires_at)
            if actual != expected:
                failures.append({"test": name, "input": {
                    "now": now, "expires_at": expires_at},
                    "expected": expected, "actual": actual})
        except Exception as error:
            errors.append({"test": name, "error": repr(error)})
    # 用脚本内容哈希标识这次测试的演示代码版本。
    revision = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return {
        "revision": revision,
        "status": "error" if errors else "failed" if failures else "passed",
        "tests_run": len(cases),
        "failure_count": len(failures),
        "error_count": len(errors),
        "failures": failures,
        "errors": errors,
        "output_ref": str(output_ref.resolve()),
        "startup_log": "[教学填充] 启动和依赖检查信息\n" * 2000,
    }


def compact_test_result(result, max_failures=3):
    if max_failures < 1:
        raise ValueError("至少保留一条失败证据")
    # 字段缺失直接报错，不把解析失败当作测试通过。
    keys = ("revision", "status", "tests_run", "failure_count",
            "error_count", "errors", "output_ref")
    summary = {key: result[key] for key in keys}
    failures = result["failures"]
    summary["failures"] = failures[:max_failures]
    summary["omitted_failures"] = len(failures) - len(summary["failures"])
    return summary


def main():
    path = Path("artifacts/test_result.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    result = run_tests(path)
    full = json.dumps(result, ensure_ascii=False, indent=2)
    path.write_text(full, encoding="utf-8")
    short = json.dumps(compact_test_result(result), ensure_ascii=False, indent=2)
    print(f"完整结果 {len(full)} 字符 → 短结果 {len(short)} 字符（不是 Token 数）")
    print(short)
    print("完整记录已保存，可按 output_ref 补读。短结果仍需经过预算检查。")


if __name__ == "__main__":
    main()
