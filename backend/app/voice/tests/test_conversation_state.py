from app.voice.pipeline.conversation_state import ConversationState


def test_add_and_retrieve_order():
    c = ConversationState()
    c.add_user("Salom")
    c.add_assistant("Salom! Qanday yordam bera olaman?")
    msgs = c.messages()
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[0].text == "Salom"


def test_blank_messages_ignored():
    c = ConversationState()
    c.add_user("   ")
    assert c.messages() == []


def test_history_is_bounded():
    c = ConversationState(max_turns=4)
    for i in range(10):
        c.add_user(f"q{i}")
    msgs = c.messages()
    assert len(msgs) == 4
    assert msgs[-1].text == "q9"  # newest kept


def test_clear():
    c = ConversationState()
    c.add_user("x")
    c.clear()
    assert c.messages() == []
