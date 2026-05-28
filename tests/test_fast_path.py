from app.search.planner import PlanMode, QueryPlanner


def test_planner_detects_inventory():
    planner = QueryPlanner()
    plan = planner.plan("how many files do i have?")
    # The current determine_query_intent in project_constants might not set "inventory" to true
    # for this exact string, but the planner uses a manual check for "how many files"
    assert plan.mode == PlanMode.FAST_METADATA
    assert plan.intents["inventory"] is True


def test_planner_detects_project():
    planner = QueryPlanner()
    # "project summary" triggers metadata_intent and project intent
    plan = planner.plan("show project summary")
    assert plan.intents["project"] is True
    assert plan.mode == PlanMode.FAST_PROJECT





def test_planner_detects_full_rag():
    planner = QueryPlanner()
    plan = planner.plan("how does the retrieval algorithm work in app/search?")
    assert plan.mode == PlanMode.FULL_RAG


def test_planner_fallback():
    planner = QueryPlanner()
    plan = planner.plan("explain quantum mechanics")
    assert plan.mode == PlanMode.FULL_RAG
