from windows_pet.conversation import Conversation

def test_remove_last_user_is_exact_and_safe():
    c=Conversation(); c.add_user("old"); c.add_assistant("answer")
    assert not c.remove_last_user("old")
    c.add_user("new"); assert c.remove_last_user("new")
    assert c.messages()==[{"role":"user","content":"old"},{"role":"assistant","content":"answer"}]
