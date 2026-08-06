import json
from pathlib import Path
from PySide6.QtCore import QPoint, QRect
from windows_pet.storage import constrain_to_primary, load_position, save_position
from windows_pet.animation import load_animations
from windows_pet.chat_bubble import ChatBubble, chat_position

ROOT = Path(__file__).parents[1]
def test_manifest_and_variable_frame_counts(qapp):
    animations = load_animations(ROOT / 'assets' / 'animations')
    assert len(animations['idle'].frames) == 4
    assert len(animations['thinking'].frames) == 3
def test_missing_asset_error(tmp_path, qapp):
    manifest = {'animations': {'x': {'frame_count': 1, 'fps_recommended': 1, 'frames': [{'file':'missing.png'}]}}}
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    try: load_animations(tmp_path)
    except RuntimeError as exc: assert '素材' in str(exc)
    else: assert False
def test_position_round_trip(tmp_path):
    p = tmp_path/'position.json'; save_position(p, QPoint(12, 34)); assert load_position(p) == QPoint(12, 34)
def test_position_is_constrained():
    assert constrain_to_primary(QPoint(-50, 900), QRect(0, 0, 1920, 1080), 200) == QPoint(0, 880)

def test_chat_position_prefers_right_then_left_and_clamps():
    screen = QRect(0, 0, 1000, 800)
    assert chat_position(QRect(100, 300, 100, 100), screen, (380, 460)).x() == 211
    assert chat_position(QRect(850, 300, 100, 100), screen, (380, 460)).x() == 458
    assert chat_position(QRect(400, 0, 100, 100), screen, (380, 460)).y() == 0
    assert chat_position(QRect(400, 750, 100, 50), screen, (380, 460)).y() == 340

def test_chat_rejects_blank_and_blocks_duplicate(qapp):
    class Pet:
        def __init__(self): self.plays = []
        def play(self, name): self.plays.append(name)
    pet = Pet(); chat = ChatBubble(pet)
    chat.input.setPlainText("  \n")
    assert chat.send_message() is False and not chat.pending
    chat.input.setPlainText("hello")
    assert chat.send_message() is True and chat.pending
    assert chat.send_message() is False
    assert pet.plays == ["thinking"]
    chat.close()
