from __future__ import annotations

import ast
from pathlib import Path

from app.runtime import output
from app.runtime.overlay_renderer import OverlayRenderer
from app.runtime.visual_publish_scheduler import VisualPublishScheduler
from app.runtime.worker_metrics_publisher import WorkerMetricsPublisher


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    return modules


def test_output_facade_reexports_the_concrete_classes():
    assert output.OverlayRenderer is OverlayRenderer
    assert output.VisualPublishScheduler is VisualPublishScheduler
    assert output.WorkerMetricsPublisher is WorkerMetricsPublisher
    assert output.__all__ == [
        "OverlayRenderer",
        "VisualPublishScheduler",
        "WorkerMetricsPublisher",
    ]


def test_output_facade_contains_no_implementation_classes():
    facade_path = PROJECT_ROOT / "app/runtime/output.py"
    tree = ast.parse(facade_path.read_text(encoding="utf-8"))

    assert not any(isinstance(node, ast.ClassDef) for node in tree.body)
    assert len(facade_path.read_text(encoding="utf-8").splitlines()) <= 15


def test_internal_consumers_do_not_depend_on_output_facade():
    internal_paths = [
        PROJECT_ROOT / "app/runtime/worker_base.py",
        PROJECT_ROOT / "app/runtime/worker_visual_publisher.py",
        PROJECT_ROOT / "app/runtime/__init__.py",
        PROJECT_ROOT / "app/runtime/overlay_renderer.py",
        PROJECT_ROOT / "app/runtime/visual_publish_scheduler.py",
        PROJECT_ROOT / "app/runtime/worker_metrics_publisher.py",
    ]

    for path in internal_paths:
        assert "app.runtime.output" not in imported_modules(path), path


def test_overlay_direction_colors_remain_stable_after_module_split():
    renderer = OverlayRenderer()

    assert renderer.get_line_color("a_to_b") == renderer.LINE_A_TO_B_COLOR
    assert renderer.get_line_color("b_to_a") == renderer.LINE_B_TO_A_COLOR
    assert renderer.get_line_color("any") == renderer.LINE_ANY_COLOR
