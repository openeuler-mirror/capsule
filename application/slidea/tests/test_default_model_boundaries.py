import ast
import inspect
import unittest
from pathlib import Path


SLIDEA_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOTS = [
    SLIDEA_ROOT / "core",
    SLIDEA_ROOT / "scripts",
    SLIDEA_ROOT / "skill" / "slidea" / "references",
]
FORBIDDEN_MARKERS = [
    "SLIDEA_" + "MODE",
    "MODEL_INVOKE_" + "HANDOVER",
    "PREMIUM_" + "LLM_",
    "Model" + "Route",
    "work_" + "node",
    "X-Agent" + "-Id",
    "X-Work" + "-Node",
    "Agent" + "Profile",
]


class DefaultModelBoundaryTests(unittest.TestCase):
    def test_runtime_has_no_removed_model_selection_markers(self):
        violations = []
        for root in RUNTIME_ROOTS:
            for source_file in root.rglob("*.py"):
                source = source_file.read_text(encoding="utf-8")
                for marker in FORBIDDEN_MARKERS:
                    if marker in source:
                        violations.append(
                            f"{source_file.relative_to(SLIDEA_ROOT)} contains {marker}"
                        )
        self.assertEqual(violations, [])

    def test_chat_client_is_only_constructed_by_the_model_gateway(self):
        allowed_file = SLIDEA_ROOT / "core" / "utils" / "llm.py"
        violations = []
        for root in RUNTIME_ROOTS:
            for source_file in root.rglob("*.py"):
                if source_file == allowed_file:
                    continue
                tree = ast.parse(
                    source_file.read_text(encoding="utf-8"),
                    filename=str(source_file),
                )
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module == "langchain_openai":
                        if any(alias.name == "ChatOpenAI" for alias in node.names):
                            violations.append(
                                f"{source_file.relative_to(SLIDEA_ROOT)}:{node.lineno}"
                            )
                    elif isinstance(node, ast.Import):
                        if any(alias.name == "langchain_openai" for alias in node.names):
                            violations.append(
                                f"{source_file.relative_to(SLIDEA_ROOT)}:{node.lineno}"
                            )
        self.assertEqual(violations, [])

    def test_document_parser_does_not_accept_model_clients(self):
        from core.utils.document_parser.config import ImageConfig, PDFEngineConfig

        self.assertNotIn("vlm_model", inspect.signature(PDFEngineConfig).parameters)
        self.assertNotIn("vlm_model", inspect.signature(ImageConfig).parameters)

    def test_model_invocation_calls_do_not_select_clients(self):
        violations = []
        for root in RUNTIME_ROOTS:
            for source_file in root.rglob("*.py"):
                tree = ast.parse(
                    source_file.read_text(encoding="utf-8"),
                    filename=str(source_file),
                )
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                        continue
                    if node.func.id in {"llm_invoke", "vlm_invoke"} and len(node.args) > 2:
                        violations.append(
                            f"{source_file.relative_to(SLIDEA_ROOT)}:{node.lineno}"
                        )
                    if node.func.id == "vlm_raw_invoke" and len(node.args) > 1:
                        violations.append(
                            f"{source_file.relative_to(SLIDEA_ROOT)}:{node.lineno}"
                        )
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
