def test_the_architect_still_imports_the_structured_helper_from_its_old_place():
    from blueprints.architect import llm
    from core.agent import structured
    assert llm.structured is structured.structured and llm.StepFailed is structured.StepFailed
