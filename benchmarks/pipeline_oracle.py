"""Independent Acceptance Oracle Grader for MiniPipeline greenfield evaluation.
This test suite is NOT revealed to agents during construction.
It acts as the strict external ground-truth oracle for DAG execution,
cycle detection, retry mechanisms, and context propagation.
"""
import sys
import pytest

def get_pipeline_cls():
    for mod in list(sys.modules.keys()):
        if "mini_pipeline" in mod:
            del sys.modules[mod]
    try:
        from mini_pipeline.pipeline import Pipeline, CyclicDependencyError
        return Pipeline, CyclicDependencyError
    except ImportError:
        try:
            from src.mini_pipeline.pipeline import Pipeline, CyclicDependencyError
            return Pipeline, CyclicDependencyError
        except ImportError:
            raise ImportError("Could not find Pipeline in mini_pipeline.pipeline or src.mini_pipeline.pipeline")

def test_01_single_task():
    Pipeline, _ = get_pipeline_cls()
    p = Pipeline()
    p.add_task("task1", lambda ctx: 42)
    res = p.run()
    assert res.success is True
    assert res.results == {"task1": 42}
    assert res.execution_order == ["task1"]
    assert res.failed_tasks == []

def test_02_linear_dependency():
    Pipeline, _ = get_pipeline_cls()
    p = Pipeline()
    # Add in reverse order: C depends on B, B depends on A
    p.add_task("C", lambda ctx: ctx["B"] + 1, depends_on=["B"])
    p.add_task("B", lambda ctx: ctx["A"] * 2, depends_on=["A"])
    p.add_task("A", lambda ctx: 10)
    res = p.run()
    assert res.success is True
    assert res.execution_order == ["A", "B", "C"]
    assert res.results["A"] == 10
    assert res.results["B"] == 20
    assert res.results["C"] == 21

def test_03_diamond_dependency():
    Pipeline, _ = get_pipeline_cls()
    p = Pipeline()
    # A -> B, A -> C, (B, C) -> D
    p.add_task("A", lambda ctx: 1)
    p.add_task("B", lambda ctx: ctx["A"] + 10, depends_on=["A"])
    p.add_task("C", lambda ctx: ctx["A"] + 20, depends_on=["A"])
    p.add_task("D", lambda ctx: ctx["B"] + ctx["C"], depends_on=["B", "C"])
    res = p.run()
    assert res.success is True
    assert res.execution_order[0] == "A"
    assert res.execution_order[-1] == "D"
    assert set(res.execution_order[1:3]) == {"B", "C"}
    assert res.results["D"] == 11 + 21

def test_04_cyclic_dependency_simple():
    Pipeline, CyclicDependencyError = get_pipeline_cls()
    p = Pipeline()
    p.add_task("A", lambda ctx: 1, depends_on=["B"])
    p.add_task("B", lambda ctx: 2, depends_on=["A"])
    with pytest.raises(CyclicDependencyError):
        p.run()

def test_05_cyclic_dependency_indirect():
    Pipeline, CyclicDependencyError = get_pipeline_cls()
    p = Pipeline()
    p.add_task("A", lambda ctx: 1, depends_on=["B"])
    p.add_task("B", lambda ctx: 2, depends_on=["C"])
    p.add_task("C", lambda ctx: 3, depends_on=["A"])
    with pytest.raises(CyclicDependencyError):
        p.run()

def test_06_self_dependency():
    Pipeline, CyclicDependencyError = get_pipeline_cls()
    p = Pipeline()
    p.add_task("A", lambda ctx: 1, depends_on=["A"])
    with pytest.raises(CyclicDependencyError):
        p.run()

def test_07_missing_dependency():
    Pipeline, _ = get_pipeline_cls()
    p = Pipeline()
    p.add_task("A", lambda ctx: 1, depends_on=["non_existent"])
    with pytest.raises(ValueError):
        p.run()

def test_08_duplicate_task_name():
    Pipeline, _ = get_pipeline_cls()
    p = Pipeline()
    p.add_task("A", lambda ctx: 1)
    with pytest.raises(ValueError):
        p.add_task("A", lambda ctx: 2)

def test_09_invalid_task_args():
    Pipeline, _ = get_pipeline_cls()
    p = Pipeline()
    with pytest.raises(TypeError):
        p.add_task(123, lambda ctx: 1)  # name must be str
    with pytest.raises(TypeError):
        p.add_task("A", "not_a_callable")

def test_10_negative_retries():
    Pipeline, _ = get_pipeline_cls()
    p = Pipeline()
    with pytest.raises(ValueError):
        p.add_task("A", lambda ctx: 1, max_retries=-1)

def test_11_retry_success():
    Pipeline, _ = get_pipeline_cls()
    p = Pipeline()
    attempts = [0]
    def flaky_task(ctx):
        attempts[0] += 1
        if attempts[0] < 2:
            raise RuntimeError("Transient error")
        return "success"

    p.add_task("flaky", flaky_task, max_retries=2)
    res = p.run()
    assert res.success is True
    assert res.results["flaky"] == "success"
    assert attempts[0] == 2

def test_12_retry_exhausted():
    Pipeline, _ = get_pipeline_cls()
    p = Pipeline()
    attempts = [0]
    def failing_task(ctx):
        attempts[0] += 1
        raise RuntimeError("Permanent error")

    p.add_task("fail", failing_task, max_retries=2)
    res = p.run()
    assert res.success is False
    assert "fail" in res.failed_tasks
    assert "Permanent error" in res.errors["fail"]
    assert attempts[0] == 3  # initial + 2 retries

def test_13_downstream_skipped_on_failure():
    Pipeline, _ = get_pipeline_cls()
    p = Pipeline()
    b_called = [False]
    c_called = [False]

    def task_a(ctx):
        raise ValueError("A exploded")
    def task_b(ctx):
        b_called[0] = True
        return 100
    def task_c(ctx):
        c_called[0] = True
        return 200

    p.add_task("A", task_a)
    p.add_task("B", task_b, depends_on=["A"])  # should be skipped because A failed
    p.add_task("C", task_c)  # independent, should execute

    res = p.run()
    assert res.success is False
    assert "A" in res.failed_tasks
    assert b_called[0] is False
    assert c_called[0] is True
    assert res.results["C"] == 200

def test_14_context_propagation():
    Pipeline, _ = get_pipeline_cls()
    p = Pipeline()
    p.add_task("step1", lambda ctx: ctx.get("base", 0) + 5)
    p.add_task("step2", lambda ctx: ctx["step1"] * 2, depends_on=["step1"])
    res = p.run(initial_context={"base": 10})
    assert res.success is True
    assert res.results["step1"] == 15
    assert res.results["step2"] == 30

def test_15_empty_pipeline():
    Pipeline, _ = get_pipeline_cls()
    p = Pipeline()
    res = p.run()
    assert res.success is True
    assert res.results == {}
    assert res.execution_order == []
    assert res.failed_tasks == []
