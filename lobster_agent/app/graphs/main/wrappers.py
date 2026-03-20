"""Wrapper nodes for invoking subgraphs and mapping results to MainState.

These nodes handle the boundary between Parent Graph and Subgraphs.
They invoke subgraphs and map the returned results into MainState fields.
"""

from datetime import datetime

from app.schemas import TraceEntry
from app.graphs.research import create_research_graph, create_research_input
from app.graphs.execution import create_execution_graph, create_execution_input
from app.graphs.review import create_review_graph, create_review_input
from .state import MainState, ResearchResult, ExecutionResult, ReviewResult


def invoke_research_subgraph(state: MainState) -> MainState:
    """Invoke Research Subgraph and map results to MainState.

    This is a real wrapper that:
    1. Maps MainState → ResearchState input
    2. Invokes research subgraph
    3. Maps ResearchState output → MainState

    Handles failures by creating structured Error and failure TraceEntry.
    """
    from app.schemas import Error

    try:
        # Step 1: Map MainState to ResearchState input
        normalized_task = state.get("normalized_task") or {}
        research_input = create_research_input(
            research_query=normalized_task.get("objective", state["user_request"]),
            context={
                "user_request": state["user_request"],
                "messages": state.get("messages", []),
                "workspace_context": state.get("workspace_context", ""),
            },
            task_id=state["task_id"]
        )

        # Step 2: Invoke research subgraph
        research_graph = create_research_graph()
        compiled_research = research_graph.compile()
        research_output = compiled_research.invoke(research_input)

        # Step 3: Map ResearchState output to ResearchResult
        research_result: ResearchResult = {
            "summary": research_output["summary"],
            "evidence": research_output["evidence"],
            "citations": list(source.get("title", "Unknown") for source in research_output.get("filtered_sources", [])),
            "confidence": research_output["confidence"],
            "open_questions": research_output["open_questions"],
        }

        # Create success trace entry
        trace_entry = TraceEntry(
            step_id=f"trace_{len(state.get('trace', []))}",
            node_name="invoke_research_subgraph",
            subgraph="research",
            timestamp=datetime.utcnow(),
            input_summary=f"task: '{state.get('normalized_task', {}).get('objective', '')[:50]}...'",
            output_summary=f"confidence: {research_result['confidence']}, evidence: {len(research_result['evidence'])} items",
            status="success",
        )

        return {
            **state,
            "current_subgraph": "research",
            "research_result": research_result,
            "trace": [*state.get("trace", []), trace_entry],
        }

    except Exception as e:
        # Create structured error
        error = Error(
            code="RESEARCH_SUBGRAPH_FAILED",
            severity="error",
            message=f"Research subgraph invocation failed: {str(e)}",
            source="main.wrappers.invoke_research_subgraph",
            timestamp=datetime.utcnow(),
            stack_trace=None  # Could add traceback.format_exc() in Phase 2
        )

        # Create failure trace entry
        trace_entry = TraceEntry(
            step_id=f"trace_{len(state.get('trace', []))}",
            node_name="invoke_research_subgraph",
            subgraph="research",
            timestamp=datetime.utcnow(),
            input_summary=f"task: '{state.get('normalized_task', {}).get('objective', '')[:50]}...'",
            output_summary=f"FAILED: {str(e)[:100]}",
            status="failure",
        )

        return {
            **state,
            "current_subgraph": "research",
            "errors": [*state.get("errors", []), error],
            "trace": [*state.get("trace", []), trace_entry],
            "status": "partial",  # Mark as partial since research failed but workflow continues
        }


def invoke_execution_subgraph(state: MainState) -> MainState:
    """Invoke Execution Subgraph and map results to MainState.

    This is a real wrapper that:
    1. Maps MainState → ExecutionState input
    2. Invokes execution subgraph
    3. Maps ExecutionState output → MainState

    Handles failures by creating structured Error and failure TraceEntry.
    """
    from app.schemas import Error

    try:
        # Step 1: Map MainState to ExecutionState input
        normalized_task = state.get("normalized_task") or {}
        execution_input = create_execution_input(
            task_description=normalized_task.get("objective", state["user_request"]),
            context={
                "user_request": state["user_request"],
                "research_result": state.get("research_result"),
                "workspace_context": state.get("workspace_context", ""),
            },
            task_id=state["task_id"]
        )

        # Step 2: Invoke execution subgraph
        execution_graph = create_execution_graph()
        compiled_execution = execution_graph.compile()
        execution_output = compiled_execution.invoke(execution_input)

        # Step 3: Map ExecutionState output to ExecutionResult.
        # Read from the typed ExecutionOutput when available (set by execute node),
        # falling back to top-level ExecutionState fields for the safety-abort case
        # where execute never ran and execution_result is None.
        exec_out = execution_output.get("execution_result") or {}

        # When safety check blocked execution, execution_result is never set.
        # Use "no_op" as the default status (not "failed") so the review verdict
        # correctly classifies this as "blocked" rather than "fail".
        safety_check_result = execution_output.get("safety_check") or {}
        safety_blocked = not safety_check_result.get("passed", True)
        default_status = "no_op" if safety_blocked else "failed"

        execution_result: ExecutionResult = {
            "actions_taken": exec_out.get("actions_taken", []),
            "artifacts": exec_out.get("artifacts", execution_output.get("artifacts", [])),
            "logs": exec_out.get("logs", execution_output.get("logs", [])),
            "success": exec_out.get("success", execution_output.get("success", False)),
            "status": exec_out.get("status", default_status),
            "errors": exec_out.get("errors", execution_output.get("errors", [])),
        }

        # Determine trace status based on execution success
        trace_status = "success" if execution_result["success"] else "partial"

        # Create trace entry
        trace_entry = TraceEntry(
            step_id=f"trace_{len(state.get('trace', []))}",
            node_name="invoke_execution_subgraph",
            subgraph="execution",
            timestamp=datetime.utcnow(),
            input_summary=f"task: '{state.get('normalized_task', {}).get('objective', '')[:50]}...'",
            output_summary=f"success: {execution_result['success']}, actions: {len(execution_result['actions_taken'])}",
            status=trace_status,
        )

        # Propagate execution errors and artifacts to MainState top-level fields
        updated_errors = [*state.get("errors", []), *execution_result["errors"]]
        updated_artifacts = [*state.get("artifacts", []), *execution_result["artifacts"]]

        return {
            **state,
            "current_subgraph": "execution",
            "execution_result": execution_result,
            "artifacts": updated_artifacts,
            "errors": updated_errors,
            "trace": [*state.get("trace", []), trace_entry],
        }

    except Exception as e:
        # Create structured error
        error = Error(
            code="EXECUTION_SUBGRAPH_FAILED",
            severity="critical",
            message=f"Execution subgraph invocation failed: {str(e)}",
            source="main.wrappers.invoke_execution_subgraph",
            timestamp=datetime.utcnow(),
            stack_trace=None
        )

        # Create failure trace entry
        trace_entry = TraceEntry(
            step_id=f"trace_{len(state.get('trace', []))}",
            node_name="invoke_execution_subgraph",
            subgraph="execution",
            timestamp=datetime.utcnow(),
            input_summary=f"task: '{state.get('normalized_task', {}).get('objective', '')[:50]}...'",
            output_summary=f"FAILED: {str(e)[:100]}",
            status="failure",
        )

        return {
            **state,
            "current_subgraph": "execution",
            "errors": [*state.get("errors", []), error],
            "trace": [*state.get("trace", []), trace_entry],
            "status": "failed",  # Execution failure is critical, mark as failed
        }


def invoke_review_subgraph(state: MainState) -> MainState:
    """Invoke Review Subgraph and map results to MainState.

    This is a real wrapper that:
    1. Maps MainState → ReviewState input
    2. Invokes review subgraph
    3. Maps ReviewState output → MainState

    Handles failures by creating structured Error and failure TraceEntry.

    NOTE: Review wrapper only returns review_result. The final workflow status
    is determined by the finalize_response node, which considers:
    - review verdict
    - execution success
    - errors present

    This keeps responsibility clear: wrapper = invoke + map, finalize = decide status.
    """
    from app.schemas import Error

    try:
        # Step 1: Map MainState to ReviewState input
        normalized_task = state.get("normalized_task") or {}
        review_input = create_review_input(
            review_target={
                "execution_result": state.get("execution_result"),
                "artifacts": state.get("artifacts", []),
                "research_result": state.get("research_result"),
            },
            expected_outputs=normalized_task.get("requested_outputs", ["response"]),
            task_id=state["task_id"]
        )

        # Step 2: Invoke review subgraph
        review_graph = create_review_graph()
        compiled_review = review_graph.compile()
        review_output = compiled_review.invoke(review_input)

        # Step 3: Map ReviewState output to ReviewResult
        review_result: ReviewResult = {
            "verdict": review_output["verdict"],
            "issues": [issue["description"] for issue in review_output["issues"]],
            "approved_artifacts": review_output["approved_artifacts"],
            "final_response": review_output["final_response"],
            "review_notes": review_output["review_notes"],
        }

        # Create trace entry
        trace_entry = TraceEntry(
            step_id=f"trace_{len(state.get('trace', []))}",
            node_name="invoke_review_subgraph",
            subgraph="review",
            timestamp=datetime.utcnow(),
            input_summary=f"reviewing execution results",
            output_summary=f"verdict: {review_result['verdict']}, issues: {len(review_result['issues'])}",
            status="success",
        )

        # Wrapper only provides review_result; finalize_response decides final status
        return {
            **state,
            "current_subgraph": "review",
            "review_result": review_result,
            "trace": [*state.get("trace", []), trace_entry],
        }

    except Exception as e:
        # Create structured error
        error = Error(
            code="REVIEW_SUBGRAPH_FAILED",
            severity="error",
            message=f"Review subgraph invocation failed: {str(e)}",
            source="main.wrappers.invoke_review_subgraph",
            timestamp=datetime.utcnow(),
            stack_trace=None
        )

        # Create failure trace entry
        trace_entry = TraceEntry(
            step_id=f"trace_{len(state.get('trace', []))}",
            node_name="invoke_review_subgraph",
            subgraph="review",
            timestamp=datetime.utcnow(),
            input_summary=f"reviewing execution results",
            output_summary=f"FAILED: {str(e)[:100]}",
            status="failure",
        )

        return {
            **state,
            "current_subgraph": "review",
            "errors": [*state.get("errors", []), error],
            "trace": [*state.get("trace", []), trace_entry],
            "status": "partial",  # Review failure means partial success
        }
